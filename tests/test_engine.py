"""
Tests for the Execution Engine (Phase 4).

Covers:
  - Happy path: direct answer (no tool calls)
  - Tool execution loop: plan → execute → reflect → answer
  - Multi-iteration tool loops
  - Error handling: fatal, retryable, recoverable
  - Budget exhaustion → SUSPENDED
  - Iteration limit → graceful degradation
  - Resume from checkpoint
  - Crash recovery: engine-level exceptions

We use fakes for all dependencies: StateMachine (real),
ToolRegistry (mock), CheckpointManager (mock), LLM Gateway (mock).
The real StateMachine is used because it's already tested and
its transitions are critical to verify the engine drives correctly.
"""

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentworks.checkpoint import CheckpointManager
from agentworks.engine import ExecutionEngine
from agentworks.state_machine import (
    AgentState,
    ExecutionContext,
    Message,
    create_agent_state_machine,
)

# --------------------------------------------------------------------------
# Fake LLM responses
# --------------------------------------------------------------------------


@dataclass
class FakeLLMUsage:
    prompt_tokens: int = 100
    completion_tokens: int = 50


@dataclass
class FakeModelCost:
    input_per_1k: float = 0.003
    output_per_1k: float = 0.015


@dataclass
class FakeToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def model_dump(self) -> dict[str, Any]:
        """Mimic Pydantic's model_dump() so the engine can serialize this."""
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class FakeLLMResponse:
    """Simulates the LLM gateway response object."""

    content: str | None = None
    tool_calls: list[FakeToolCall] | None = None
    usage: FakeLLMUsage | None = None
    model_cost: FakeModelCost | None = None


def make_final_answer(text: str = "Here is my answer.") -> FakeLLMResponse:
    """LLM response that's a direct text answer (no tool calls)."""
    return FakeLLMResponse(
        content=text,
        usage=FakeLLMUsage(),
        model_cost=FakeModelCost(),
    )


def make_tool_call(
    tool_name: str = "test_search",
    arguments: dict | None = None,
    call_id: str = "call_001",
) -> FakeLLMResponse:
    """LLM response that requests a tool call."""
    return FakeLLMResponse(
        content=f"I need to use {tool_name}",
        tool_calls=[
            FakeToolCall(
                id=call_id,
                name=tool_name,
                arguments=arguments or {"query": "test"},
            )
        ],
        usage=FakeLLMUsage(),
        model_cost=FakeModelCost(),
    )


# --------------------------------------------------------------------------
# Fake tool registry
# --------------------------------------------------------------------------


class FakeToolResult:
    def __init__(
        self,
        success: bool = True,
        output: Any = None,
        error: str | None = None,
        error_type: str | None = None,
        latency_ms: float = 10.0,
        retry_count: int = 0,
    ):
        self.success = success
        self.output = output or {"results": ["item1"]}
        self.error = error
        self.error_type = error_type
        self.latency_ms = latency_ms
        self.retry_count = retry_count


# --------------------------------------------------------------------------
# Helper: create engine with fakes
# --------------------------------------------------------------------------


def make_engine(
    llm_responses: list | None = None,
    tool_results: list | None = None,
    checkpoint_save_side_effect=None,
) -> tuple[ExecutionEngine, AsyncMock, AsyncMock, AsyncMock]:
    """
    Create an ExecutionEngine wired to fakes.

    Returns (engine, mock_llm, mock_tools, mock_checkpoint).
    """
    sm = create_agent_state_machine()

    # LLM Gateway mock
    mock_llm = AsyncMock()
    if llm_responses:
        mock_llm.complete = AsyncMock(side_effect=llm_responses)
    else:
        mock_llm.complete = AsyncMock(return_value=make_final_answer())

    # Tool Registry mock
    mock_tools = AsyncMock()
    mock_tools.get_llm_tool_specs = MagicMock(
        return_value=[
            {
                "type": "function",
                "function": {
                    "name": "test_search",
                    "description": "Search tool",
                    "parameters": {"type": "object"},
                },
            }
        ]
    )
    if tool_results:
        mock_tools.execute = AsyncMock(side_effect=tool_results)
    else:
        mock_tools.execute = AsyncMock(return_value=FakeToolResult(success=True))

    # Checkpoint Manager mock
    mock_checkpoint = AsyncMock(spec=CheckpointManager)
    mock_checkpoint.save = AsyncMock(return_value="checksum123")
    mock_checkpoint.promote_to_cold = AsyncMock()
    mock_checkpoint.restore = AsyncMock(return_value=None)
    if checkpoint_save_side_effect:
        mock_checkpoint.save.side_effect = checkpoint_save_side_effect

    engine = ExecutionEngine(
        state_machine=sm,
        tool_registry=mock_tools,
        checkpoint_mgr=mock_checkpoint,
        llm_gateway=mock_llm,
    )

    return engine, mock_llm, mock_tools, mock_checkpoint


def make_context(**overrides) -> ExecutionContext:
    defaults = dict(
        run_id="run-test-001",
        agent_id="agent-test",
        team_id="team-test",
        current_state=AgentState.IDLE,
        max_iterations=10,
        max_budget_usd=1.0,
    )
    defaults.update(overrides)
    ctx = ExecutionContext(**defaults)
    ctx.messages.append(Message(role="user", content="Hello, agent!"))
    return ctx


# --------------------------------------------------------------------------
# Happy path: direct answer
# --------------------------------------------------------------------------


class TestDirectAnswer:
    async def test_llm_gives_final_answer(self):
        """Simplest case: LLM responds with text, no tool calls."""
        engine, mock_llm, _, _ = make_engine(llm_responses=[make_final_answer("The answer is 42.")])
        ctx = make_context()

        result = await engine.run(ctx)

        assert result.current_state == AgentState.COMPLETED
        assert result.completed_at is not None
        # LLM called exactly once
        assert mock_llm.complete.call_count == 1
        # Final message is the answer
        assert result.messages[-1].content == "The answer is 42."
        assert result.messages[-1].role == "assistant"

    async def test_completed_run_promotes_checkpoint(self):
        engine, _, _, mock_cp = make_engine(llm_responses=[make_final_answer()])
        ctx = make_context()

        await engine.run(ctx)

        mock_cp.promote_to_cold.assert_called_once_with(ctx.run_id)


# --------------------------------------------------------------------------
# Tool execution loop
# --------------------------------------------------------------------------


class TestToolLoop:
    async def test_single_tool_call_then_answer(self):
        """LLM calls one tool, sees result, gives answer."""
        engine, mock_llm, mock_tools, _ = make_engine(
            llm_responses=[
                make_tool_call("test_search", {"query": "weather"}),
                make_final_answer("It's sunny."),
            ],
            tool_results=[
                FakeToolResult(
                    success=True,
                    output={"results": ["sunny", "72F"]},
                ),
            ],
        )
        ctx = make_context()

        result = await engine.run(ctx)

        assert result.current_state == AgentState.COMPLETED
        # Tool was executed once
        mock_tools.execute.assert_called_once()
        # LLM was called twice (plan + reflect→plan→answer)
        assert mock_llm.complete.call_count == 2
        # Tool call is recorded
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "test_search"

    async def test_multiple_tool_calls_before_answer(self):
        """LLM calls tools twice, then gives answer."""
        engine, mock_llm, mock_tools, _ = make_engine(
            llm_responses=[
                make_tool_call("test_search", {"query": "q1"}, "call_001"),
                make_tool_call("test_search", {"query": "q2"}, "call_002"),
                make_final_answer("Combined answer."),
            ],
            tool_results=[
                FakeToolResult(success=True, output={"results": ["r1"]}),
                FakeToolResult(success=True, output={"results": ["r2"]}),
            ],
        )
        ctx = make_context()

        result = await engine.run(ctx)

        assert result.current_state == AgentState.COMPLETED
        assert mock_tools.execute.call_count == 2
        assert mock_llm.complete.call_count == 3
        assert len(result.tool_calls) == 2

    async def test_tool_result_added_to_messages(self):
        """After tool execution, its result appears in message history."""
        engine, _, _, _ = make_engine(
            llm_responses=[
                make_tool_call("test_search", {"query": "test"}),
                make_final_answer("Done."),
            ],
            tool_results=[
                FakeToolResult(success=True, output={"answer": "found"}),
            ],
        )
        ctx = make_context()

        result = await engine.run(ctx)

        # Find the tool message
        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert "found" in tool_msgs[0].content

    async def test_iteration_count_incremented(self):
        """Each tool execution cycle increments the iteration count."""
        engine, _, _, _ = make_engine(
            llm_responses=[
                make_tool_call("test_search", {"query": "q1"}, "c1"),
                make_tool_call("test_search", {"query": "q2"}, "c2"),
                make_final_answer("Done."),
            ],
            tool_results=[
                FakeToolResult(success=True),
                FakeToolResult(success=True),
            ],
        )
        ctx = make_context()

        result = await engine.run(ctx)

        # Two tool cycles = two iterations
        assert result.iteration_count == 2


# --------------------------------------------------------------------------
# Error handling: tool failures
# --------------------------------------------------------------------------


class TestToolErrors:
    async def test_fatal_tool_error_fails_run(self):
        """A fatal tool error (e.g., tool not found) fails the run."""
        engine, _, _, _ = make_engine(
            llm_responses=[
                make_tool_call("bad_tool"),
            ],
            tool_results=[
                FakeToolResult(
                    success=False,
                    error="Tool not found",
                    error_type="not_found",
                ),
            ],
        )
        ctx = make_context()

        result = await engine.run(ctx)

        assert result.current_state == AgentState.FAILED

    async def test_recoverable_tool_error_fed_back_to_llm(self):
        """Recoverable error is fed to the LLM, which then succeeds."""
        engine, mock_llm, _, _ = make_engine(
            llm_responses=[
                make_tool_call("test_search", {"bad": "input"}),
                # After seeing error, LLM gives direct answer
                make_final_answer("I'll answer without the tool."),
            ],
            tool_results=[
                FakeToolResult(
                    success=False,
                    error="Input validation failed for field 'query'",
                    error_type="invalid_input",
                ),
            ],
        )
        ctx = make_context()

        result = await engine.run(ctx)

        # The run should complete (LLM recovered)
        assert result.current_state == AgentState.COMPLETED
        # LLM was called twice: initial plan + recovery
        assert mock_llm.complete.call_count == 2
        # Error info was added to messages
        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert "error" in tool_msgs[0].content.lower()


# --------------------------------------------------------------------------
# Error handling: LLM failures
# --------------------------------------------------------------------------


class TestLLMErrors:
    async def test_fatal_llm_error_fails_run(self):
        """AuthenticationError from LLM → immediate FAILED."""
        engine, mock_llm, _, _ = make_engine()
        mock_llm.complete = AsyncMock(side_effect=Exception("AuthenticationError: Invalid API key"))
        ctx = make_context()

        result = await engine.run(ctx)

        assert result.current_state == AgentState.FAILED
        assert "AuthenticationError" in (result.last_error or "")

    async def test_retryable_llm_error_fails_run(self):
        """
        TimeoutError from LLM — classified as retryable but the engine
        currently treats exhausted retryable errors as failed.
        (Transparent retry logic is in the LLM gateway itself.)
        """
        engine, mock_llm, _, _ = make_engine()
        mock_llm.complete = AsyncMock(side_effect=Exception("TimeoutError: Connection timed out"))
        ctx = make_context()

        result = await engine.run(ctx)

        assert result.current_state == AgentState.FAILED

    async def test_recoverable_llm_error_retries(self):
        """
        A generic LLM error is recoverable — engine adds error context
        to messages and tries the planning step again.
        """
        call_count = 0

        async def flaky_llm(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Some random LLM glitch")
            return make_final_answer("Recovered!")

        engine, mock_llm, _, _ = make_engine()
        mock_llm.complete = AsyncMock(side_effect=flaky_llm)
        ctx = make_context()

        result = await engine.run(ctx)

        assert result.current_state == AgentState.COMPLETED
        # Error context was added as a system message
        system_msgs = [m for m in result.messages if m.role == "system"]
        assert any("Error occurred" in m.content for m in system_msgs)


# --------------------------------------------------------------------------
# Budget exhaustion
# --------------------------------------------------------------------------


class TestBudgetLimits:
    async def test_zero_budget_suspends_before_llm_call(self):
        """If budget is exhausted at planning time, run suspends."""
        engine, mock_llm, _, _ = make_engine()
        # Budget validator requires > 0, so use small budget + pre-consumed cost
        ctx = make_context(max_budget_usd=0.01)
        ctx.token_usage.estimated_cost_usd = 0.01  # fully consumed

        result = await engine.run(ctx)

        assert result.current_state == AgentState.SUSPENDED
        # LLM was never called
        mock_llm.complete.assert_not_called()

    async def test_budget_exhausted_during_reflection_suspends(self):
        """If budget runs out after a tool call, run suspends at reflection."""
        engine, _, _, _ = make_engine(
            llm_responses=[
                make_tool_call("test_search"),
            ],
            tool_results=[
                FakeToolResult(success=True),
            ],
        )
        # Very small budget — the LLM call adds ~$0.00105 of token cost
        # (100 prompt * $0.003/1k + 50 completion * $0.015/1k),
        # which exceeds this $0.001 budget during the first planning step.
        # After tool execution, reflection sees budget_remaining <= 0 → SUSPENDED.
        ctx = make_context(max_budget_usd=0.001)

        result = await engine.run(ctx)

        assert result.current_state == AgentState.SUSPENDED


# --------------------------------------------------------------------------
# Iteration limits
# --------------------------------------------------------------------------


class TestIterationLimits:
    async def test_iteration_limit_forces_final_answer(self):
        """At iteration limit, engine tells LLM to give best answer."""
        engine, mock_llm, _, _ = make_engine(
            llm_responses=[
                make_tool_call("test_search", call_id="c1"),
                # After reflection injects "give best answer" message,
                # LLM should give a final answer
                make_final_answer("Best answer with what I have."),
            ],
            tool_results=[
                FakeToolResult(success=True),
            ],
        )
        ctx = make_context(max_iterations=1)

        result = await engine.run(ctx)

        assert result.current_state == AgentState.COMPLETED
        # System message was injected about iteration limit
        system_msgs = [m for m in result.messages if m.role == "system"]
        assert any("maximum number of iterations" in m.content for m in system_msgs)

    async def test_hard_failure_if_llm_ignores_limit(self):
        """
        If LLM calls another tool after iteration limit,
        the planning step catches it and fails.
        """
        engine, _, _, _ = make_engine(
            llm_responses=[
                make_tool_call("test_search", call_id="c1"),
                # LLM ignores the "give best answer" and calls another tool
                make_tool_call("test_search", call_id="c2"),
            ],
            tool_results=[
                FakeToolResult(success=True),
            ],
        )
        ctx = make_context(max_iterations=1)

        result = await engine.run(ctx)

        # Should fail because iterations_remaining <= 0 in planning
        assert result.current_state == AgentState.FAILED


# --------------------------------------------------------------------------
# Resume from checkpoint
# --------------------------------------------------------------------------


class TestResume:
    async def test_resume_suspended_run(self):
        """Resume a SUSPENDED run — transitions to PLANNING and continues."""
        engine, _, _, mock_cp = make_engine(llm_responses=[make_final_answer("Resumed answer.")])

        # Create a "suspended" context snapshot
        ctx = make_context(current_state=AgentState.SUSPENDED)
        ctx.max_budget_usd = 10.0  # increase budget
        snapshot = ctx.model_dump(mode="json")

        mock_cp.restore = AsyncMock(return_value=snapshot)

        result = await engine.resume("run-test-001")

        assert result.current_state == AgentState.COMPLETED
        assert result.messages[-1].content == "Resumed answer."

    async def test_resume_nonexistent_run_raises(self):
        engine, _, _, mock_cp = make_engine()
        mock_cp.restore = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="No checkpoint found"):
            await engine.resume("nonexistent-run")


# --------------------------------------------------------------------------
# Engine-level crash handling
# --------------------------------------------------------------------------


class TestCrashHandling:
    async def test_fatal_llm_error_transitions_to_failed(self):
        """A FATAL-classified error in _step_planning → FAILED with error_history."""
        engine, mock_llm, _, _ = make_engine()
        # This matches the FATAL "auth_failure" pattern
        mock_llm.complete = AsyncMock(side_effect=Exception("AuthenticationError: Invalid API key"))
        ctx = make_context()

        result = await engine.run(ctx)

        assert result.current_state == AgentState.FAILED
        assert result.last_error is not None
        assert len(result.error_history) > 0

    async def test_crash_still_promotes_checkpoint(self):
        """Even on fatal error, checkpoint is promoted if state is terminal."""
        engine, mock_llm, _, mock_cp = make_engine()
        mock_llm.complete = AsyncMock(side_effect=Exception("AuthenticationError: key expired"))
        ctx = make_context()

        await engine.run(ctx)

        mock_cp.promote_to_cold.assert_called_once_with(ctx.run_id)

    async def test_error_history_tracks_errors(self):
        """Error history captures FATAL errors with timestamp and state."""
        engine, mock_llm, _, _ = make_engine()
        mock_llm.complete = AsyncMock(side_effect=Exception("AuthenticationError: bad credentials"))
        ctx = make_context()

        result = await engine.run(ctx)

        assert len(result.error_history) >= 1
        assert "bad credentials" in result.error_history[0]["error"]
        assert "timestamp" in result.error_history[0]
        assert "state" in result.error_history[0]

    async def test_unhandled_crash_also_tracked(self):
        """Errors that escape _step_planning are caught by run() and tracked."""
        engine, _, _, _ = make_engine()
        ctx = make_context()

        # Simulate an error that escapes the step handlers entirely
        # by making the state machine itself raise
        async def broken_step(ctx_arg):
            raise RuntimeError("Internal engine bug!")

        engine._execute_step = broken_step

        result = await engine.run(ctx)

        assert result.current_state == AgentState.FAILED
        assert "Internal engine bug!" in result.last_error
        assert len(result.error_history) >= 1


# --------------------------------------------------------------------------
# Token usage tracking
# --------------------------------------------------------------------------


class TestTokenTracking:
    async def test_tracks_llm_token_usage(self):
        """Token usage from LLM responses is accumulated."""
        engine, _, _, _ = make_engine(
            llm_responses=[
                make_tool_call("test_search"),
                make_final_answer("Done."),
            ],
            tool_results=[FakeToolResult(success=True)],
        )
        ctx = make_context()

        result = await engine.run(ctx)

        # Two LLM calls, each with 100 prompt + 50 completion tokens
        assert result.token_usage.total_tokens > 0
        assert result.token_usage.estimated_cost_usd > 0


# --------------------------------------------------------------------------
# Edge cases
# --------------------------------------------------------------------------


class TestEdgeCases:
    async def test_pending_tool_call_missing_fails(self):
        """If EXECUTING_TOOL state has no pending tool call, it's a bug → FAILED."""
        engine, _, _, _ = make_engine()

        # Manually force into EXECUTING_TOOL without setting pending_tool_call
        sm = create_agent_state_machine()
        engine_direct = ExecutionEngine(
            state_machine=sm,
            tool_registry=AsyncMock(),
            checkpoint_mgr=AsyncMock(spec=CheckpointManager),
            llm_gateway=AsyncMock(),
        )
        engine_direct._checkpoints.save = AsyncMock(return_value="cs")
        engine_direct._checkpoints.promote_to_cold = AsyncMock()

        # Force state to EXECUTING_TOOL
        ctx_direct = make_context()
        ctx_direct.current_state = AgentState.EXECUTING_TOOL
        # No pending_tool_call in metadata

        await engine_direct._step_execute_tool(ctx_direct)

        assert ctx_direct.current_state == AgentState.FAILED

    async def test_run_returns_context(self):
        """run() always returns the ExecutionContext, even on failure."""
        engine, mock_llm, _, _ = make_engine()
        mock_llm.complete = AsyncMock(side_effect=Exception("AuthenticationError: fatal"))
        ctx = make_context()

        result = await engine.run(ctx)

        assert result is ctx  # same object
        assert result.run_id == "run-test-001"


# --------------------------------------------------------------------------
# Tool scoping (tool_ids)
# --------------------------------------------------------------------------


class TestToolScoping:
    """Tests that tool_ids on ExecutionContext filters tools passed to LLM."""

    async def test_tool_ids_passed_to_get_llm_tool_specs(self):
        """When ctx.tool_ids is set, engine passes it to get_llm_tool_specs."""
        engine, _, mock_tools, _ = make_engine(llm_responses=[make_final_answer("Done.")])
        ctx = make_context(tool_ids=["search_only"])

        await engine.run(ctx)

        mock_tools.get_llm_tool_specs.assert_called_with(tool_ids=["search_only"])

    async def test_tool_ids_none_passes_none(self):
        """When ctx.tool_ids is None, engine passes None (all tools)."""
        engine, _, mock_tools, _ = make_engine(llm_responses=[make_final_answer("Done.")])
        ctx = make_context(tool_ids=None)

        await engine.run(ctx)

        mock_tools.get_llm_tool_specs.assert_called_with(tool_ids=None)

    async def test_empty_tool_ids_passes_empty_list(self):
        """When ctx.tool_ids is [], engine passes [] (no tools)."""
        engine, _, mock_tools, _ = make_engine(
            llm_responses=[make_final_answer("No tools available.")]
        )
        ctx = make_context(tool_ids=[])

        await engine.run(ctx)

        mock_tools.get_llm_tool_specs.assert_called_with(tool_ids=[])
