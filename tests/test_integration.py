"""
Integration tests for the Agent Runtime Engine (Phase 9: Production Readiness).

Tests the full lifecycle with all components wired using fakes:
  - FakeHotStore (in-memory Redis)
  - FakeColdStore (in-memory PostgreSQL)
  - Mocked LLMGateway (simulates tool call → answer flow)

No external services needed — these run in CI without Redis or Postgres.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from agentworks.api import _app_state, app
from agentworks.checkpoint import CheckpointManager
from agentworks.engine import ExecutionEngine
from agentworks.llm_gateway import CompletionResponse, ToolCallResponse
from agentworks.state_machine import (
    AgentState,
    ExecutionContext,
    Message,
    create_agent_state_machine,
)
from agentworks.tool_registry import ToolDefinition, ToolRegistry, ToolResult

# --------------------------------------------------------------------------
# Fake stores (reused from test_checkpoint.py)
# --------------------------------------------------------------------------


class FakeHotStore:
    """In-memory fake implementing the HotStore protocol."""

    def __init__(self):
        self._data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def setex(self, key: str, ttl: int, value: str | bytes) -> None:
        if isinstance(value, bytes):
            value = value.decode()
        self._data[key] = value

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def scan_iter(self, *, match: str):
        prefix = match.replace("*", "")
        for key in self._data:
            if key.startswith(prefix):
                yield key

    async def ping(self) -> bool:
        return True


class FakeColdStore:
    """In-memory fake implementing the ColdStore protocol."""

    def __init__(self):
        self._rows: list[dict[str, Any]] = []

    def acquire(self):
        return FakeColdConnection(self)


class FakeColdConnection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def __init__(self, store: FakeColdStore):
        self._store = store

    async def fetchrow(self, query: str, *args) -> dict[str, Any] | None:
        run_id = args[0] if args else None
        matches = [r for r in self._store._rows if r["run_id"] == run_id]
        if not matches:
            return None
        return max(matches, key=lambda r: r["checkpoint_version"])

    async def execute(self, query: str, *args) -> None:
        if "INSERT" in query:
            row = {
                "run_id": args[0],
                "agent_id": args[1],
                "team_id": args[2],
                "checkpoint_version": args[3],
                "state_snapshot": args[4],
                "created_at": args[5],
                "size_bytes": args[6],
                "checksum": args[7],
            }
            self._store._rows = [
                r
                for r in self._store._rows
                if not (
                    r["run_id"] == row["run_id"]
                    and r["checkpoint_version"] == row["checkpoint_version"]
                )
            ]
            self._store._rows.append(row)

    async def fetchval(self, query: str, *args):
        return 1


# --------------------------------------------------------------------------
# Full lifecycle integration test
# --------------------------------------------------------------------------


class TestFullRunLifecycle:
    """End-to-end test: start run → tool call → completed."""

    async def test_run_completes_with_tool_call(self):
        """Full lifecycle: IDLE → PLANNING → TOOL → REFLECTING → COMPLETED."""
        # Wire components
        hot = FakeHotStore()
        cold = FakeColdStore()
        checkpoint_mgr = CheckpointManager(hot_store=hot, cold_store=cold)
        state_machine = create_agent_state_machine()
        registry = ToolRegistry()

        # Register checkpoint hook (in production, observability manager does this)
        async def checkpoint_on_transition(ctx, result):
            await checkpoint_mgr.save(ctx)

        state_machine.on_transition(checkpoint_on_transition)

        # Register a tool
        await registry.register(
            ToolDefinition(
                tool_id="test_lookup",
                name="Test Lookup Tool",
                description="Look up information for testing purposes.",
                version="1.0.0",
                endpoint_url="https://api.example.com/lookup",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                owner_team="test-team",
            )
        )

        # Mock LLM gateway: first call returns tool call, second returns answer
        mock_llm = AsyncMock()
        call_count = 0

        async def mock_complete(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: LLM wants to use a tool
                return CompletionResponse(
                    content=None,
                    tool_calls=[
                        ToolCallResponse(
                            id="call-1",
                            name="test_lookup",
                            arguments={"query": "test"},
                        )
                    ],
                )
            else:
                # Second call: LLM has the answer
                return CompletionResponse(
                    content="The answer is 42.",
                    tool_calls=[],
                )

        mock_llm.complete = mock_complete

        # Mock tool execution (since we can't call real endpoints)
        async def mock_execute(tool_id, input_data, ctx=None):
            return ToolResult(
                tool_id=tool_id,
                success=True,
                output={"result": "42"},
                latency_ms=5.0,
            )

        registry.execute = mock_execute

        engine = ExecutionEngine(
            state_machine=state_machine,
            tool_registry=registry,
            checkpoint_mgr=checkpoint_mgr,
            llm_gateway=mock_llm,
        )

        # Create execution context
        ctx = ExecutionContext(
            agent_id="test-agent",
            team_id="test-team",
            max_iterations=10,
            max_budget_usd=1.0,
        )
        ctx.messages.append(Message(role="user", content="What is the answer?"))

        # Run to completion
        result = await engine.run(ctx)

        # Verify completion
        assert result.current_state == AgentState.COMPLETED
        assert result.iteration_count >= 1
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "test_lookup"
        assert result.completed_at is not None

        # Verify checkpoint was promoted to cold store
        assert len(cold._rows) == 1
        assert cold._rows[0]["run_id"] == result.run_id

        # Verify hot store is cleaned up
        assert len(hot._data) == 0

    async def test_checkpoint_retrievable_after_completion(self):
        """After run completes, checkpoint is retrievable from cold store."""
        hot = FakeHotStore()
        cold = FakeColdStore()
        checkpoint_mgr = CheckpointManager(hot_store=hot, cold_store=cold)
        state_machine = create_agent_state_machine()
        registry = ToolRegistry()

        # Register checkpoint hook
        async def checkpoint_on_transition(ctx, result):
            await checkpoint_mgr.save(ctx)

        state_machine.on_transition(checkpoint_on_transition)

        # Simple LLM that immediately gives an answer (no tool calls)
        mock_llm = AsyncMock()

        async def mock_complete(**kwargs):
            return CompletionResponse(
                content="Direct answer without tools.",
                tool_calls=[],
            )

        mock_llm.complete = mock_complete

        engine = ExecutionEngine(
            state_machine=state_machine,
            tool_registry=registry,
            checkpoint_mgr=checkpoint_mgr,
            llm_gateway=mock_llm,
        )

        ctx = ExecutionContext(
            agent_id="test-agent",
            team_id="test-team",
            max_iterations=10,
            max_budget_usd=1.0,
        )
        ctx.messages.append(Message(role="user", content="Hello"))

        result = await engine.run(ctx)
        assert result.current_state == AgentState.COMPLETED

        # Retrieve from cold store
        snapshot = await checkpoint_mgr.restore(result.run_id)
        assert snapshot is not None
        assert snapshot["run_id"] == result.run_id
        assert snapshot["current_state"] == "completed"

    def test_health_shows_engine_ok_when_wired(self):
        """Health endpoint reports engine=ok when fully wired."""
        hot = FakeHotStore()
        checkpoint_mgr = CheckpointManager(hot_store=hot)

        # Save and restore app state
        original_engine = _app_state.engine
        original_checkpoint = _app_state.checkpoint_mgr

        try:
            _app_state.engine = MagicMock()
            _app_state.checkpoint_mgr = checkpoint_mgr
            _app_state._redis_client = hot  # type: ignore[attr-defined]

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/v1/health")
            body = resp.json()

            assert body["status"] == "ok"
            assert body["checks"]["engine"] == "ok"
            assert body["checks"]["redis"] == "ok"
        finally:
            _app_state.engine = original_engine
            _app_state.checkpoint_mgr = original_checkpoint
            if hasattr(_app_state, "_redis_client"):
                delattr(_app_state, "_redis_client")
