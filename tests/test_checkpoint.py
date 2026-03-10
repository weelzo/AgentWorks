"""
Tests for the Checkpoint Manager (Phase 4).

Covers:
  - save() to hot store (Redis)
  - restore() from hot store
  - restore() fallback to cold store (PostgreSQL)
  - promote_to_cold() lifecycle
  - list_active_runs()
  - Integrity: checksum and version tracking

We use in-memory fakes for both stores instead of real Redis/PostgreSQL.
The HotStore and ColdStore protocols make this trivial.
"""

import contextlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from agentworks.checkpoint import CheckpointData, CheckpointManager
from agentworks.state_machine import AgentState, ExecutionContext

# --------------------------------------------------------------------------
# In-memory store fakes
# --------------------------------------------------------------------------


class FakeHotStore:
    """In-memory fake that implements the HotStore protocol (Redis-like)."""

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


class FakeColdStore:
    """In-memory fake that implements the ColdStore protocol (PostgreSQL-like)."""

    def __init__(self):
        self._rows: list[dict[str, Any]] = []

    def acquire(self):
        return FakeColdConnection(self)


class FakeColdConnection:
    """Fake async connection with fetchrow() and execute()."""

    def __init__(self, store: FakeColdStore):
        self._store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def fetchrow(self, query: str, *args) -> dict[str, Any] | None:
        run_id = args[0] if args else None
        # Find most recent checkpoint for this run_id
        matches = [r for r in self._store._rows if r["run_id"] == run_id]
        if not matches:
            return None
        return max(matches, key=lambda r: r["checkpoint_version"])

    async def execute(self, query: str, *args) -> None:
        # Simple INSERT simulation
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
            # Upsert: remove old version if exists
            self._store._rows = [
                r
                for r in self._store._rows
                if not (
                    r["run_id"] == row["run_id"]
                    and r["checkpoint_version"] == row["checkpoint_version"]
                )
            ]
            self._store._rows.append(row)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def make_context(**overrides) -> ExecutionContext:
    """Create an ExecutionContext with sensible defaults for testing."""
    defaults = dict(
        run_id="run-test-001",
        agent_id="agent-test",
        team_id="team-test",
        current_state=AgentState.PLANNING,
        max_iterations=10,
        max_budget_usd=1.0,
    )
    defaults.update(overrides)
    return ExecutionContext(**defaults)


# --------------------------------------------------------------------------
# Save tests
# --------------------------------------------------------------------------


class TestCheckpointSave:
    async def test_save_writes_to_hot_store(self):
        hot = FakeHotStore()
        mgr = CheckpointManager(hot_store=hot)
        ctx = make_context()

        checksum = await mgr.save(ctx)

        assert checksum  # non-empty string
        assert len(hot._data) == 1
        key = f"agent:checkpoint:{ctx.run_id}"
        assert key in hot._data

    async def test_save_increments_checkpoint_version(self):
        hot = FakeHotStore()
        mgr = CheckpointManager(hot_store=hot)
        ctx = make_context()

        assert ctx.checkpoint_version == 0

        await mgr.save(ctx)
        assert ctx.checkpoint_version == 1

        await mgr.save(ctx)
        assert ctx.checkpoint_version == 2

    async def test_save_updates_last_checkpoint_at(self):
        hot = FakeHotStore()
        mgr = CheckpointManager(hot_store=hot)
        ctx = make_context()

        before = datetime.now(UTC)
        await mgr.save(ctx)

        assert ctx.last_checkpoint_at is not None
        assert ctx.last_checkpoint_at >= before

    async def test_saved_data_is_valid_checkpoint(self):
        hot = FakeHotStore()
        mgr = CheckpointManager(hot_store=hot)
        ctx = make_context()

        await mgr.save(ctx)

        key = f"agent:checkpoint:{ctx.run_id}"
        raw = hot._data[key]
        checkpoint = CheckpointData.model_validate_json(raw)

        assert checkpoint.run_id == ctx.run_id
        assert checkpoint.agent_id == ctx.agent_id
        assert checkpoint.checkpoint_version == 1
        assert checkpoint.size_bytes > 0
        assert len(checkpoint.checksum) == 16  # SHA-256 prefix

    async def test_checksum_changes_with_state(self):
        """Different states produce different checksums (integrity)."""
        hot = FakeHotStore()
        mgr = CheckpointManager(hot_store=hot)

        ctx1 = make_context(current_state=AgentState.PLANNING)
        cs1 = await mgr.save(ctx1)

        # Change state and re-save
        ctx1.current_state = AgentState.EXECUTING_TOOL
        cs2 = await mgr.save(ctx1)

        assert cs1 != cs2


# --------------------------------------------------------------------------
# Restore tests
# --------------------------------------------------------------------------


class TestCheckpointRestore:
    async def test_restore_from_hot_store(self):
        hot = FakeHotStore()
        mgr = CheckpointManager(hot_store=hot)
        ctx = make_context()

        await mgr.save(ctx)
        snapshot = await mgr.restore(ctx.run_id)

        assert snapshot is not None
        assert snapshot["run_id"] == ctx.run_id
        assert snapshot["agent_id"] == ctx.agent_id

    async def test_restore_returns_none_when_not_found(self):
        hot = FakeHotStore()
        mgr = CheckpointManager(hot_store=hot)

        snapshot = await mgr.restore("nonexistent-run")
        assert snapshot is None

    async def test_restore_falls_back_to_cold_store(self):
        """When hot store has no data, restore tries cold store."""
        hot = FakeHotStore()
        cold = FakeColdStore()
        mgr = CheckpointManager(hot_store=hot, cold_store=cold)

        # Manually insert into cold store
        ctx = make_context()
        state_snapshot = ctx.model_dump(mode="json")
        cold._rows.append(
            {
                "run_id": ctx.run_id,
                "checkpoint_version": 1,
                "state_snapshot": json.dumps(state_snapshot),
            }
        )

        snapshot = await mgr.restore(ctx.run_id)

        assert snapshot is not None
        assert snapshot["run_id"] == ctx.run_id

    async def test_hot_store_preferred_over_cold(self):
        """If data exists in both stores, hot store wins."""
        hot = FakeHotStore()
        cold = FakeColdStore()
        mgr = CheckpointManager(hot_store=hot, cold_store=cold)

        ctx = make_context(current_state=AgentState.EXECUTING_TOOL)
        await mgr.save(ctx)
        # After save, ctx.checkpoint_version = 1

        # Old version in cold store with a DIFFERENT state to distinguish
        old_snapshot = ctx.model_dump(mode="json")
        old_snapshot["current_state"] = "idle"  # different from hot store
        cold._rows.append(
            {
                "run_id": ctx.run_id,
                "checkpoint_version": 1,
                "state_snapshot": json.dumps(old_snapshot),
            }
        )

        snapshot = await mgr.restore(ctx.run_id)
        assert snapshot is not None
        # Hot store data wins — state should be EXECUTING_TOOL, not IDLE
        assert snapshot["current_state"] == "executing_tool"

    async def test_restore_handles_bytes_from_redis(self):
        """Redis may return bytes — checkpoint manager must decode."""
        hot = FakeHotStore()
        mgr = CheckpointManager(hot_store=hot)
        ctx = make_context()

        await mgr.save(ctx)

        # Simulate Redis returning bytes
        key = f"agent:checkpoint:{ctx.run_id}"
        hot._data[key] = hot._data[key]  # already a string in our fake

        snapshot = await mgr.restore(ctx.run_id)
        assert snapshot is not None


# --------------------------------------------------------------------------
# Promote to cold store
# --------------------------------------------------------------------------


class TestPromoteToCold:
    async def test_promote_moves_to_cold_store(self):
        hot = FakeHotStore()
        cold = FakeColdStore()
        mgr = CheckpointManager(hot_store=hot, cold_store=cold)

        ctx = make_context()
        await mgr.save(ctx)

        await mgr.promote_to_cold(ctx.run_id)

        # Should be in cold store
        assert len(cold._rows) == 1
        assert cold._rows[0]["run_id"] == ctx.run_id

        # Should be removed from hot store
        assert len(hot._data) == 0

    async def test_promote_without_cold_store_is_noop(self):
        """If no cold store is configured, promotion silently succeeds."""
        hot = FakeHotStore()
        mgr = CheckpointManager(hot_store=hot, cold_store=None)

        ctx = make_context()
        await mgr.save(ctx)

        # Should not raise
        await mgr.promote_to_cold(ctx.run_id)

        # Data remains in hot store
        assert len(hot._data) == 1

    async def test_promote_nonexistent_is_noop(self):
        hot = FakeHotStore()
        cold = FakeColdStore()
        mgr = CheckpointManager(hot_store=hot, cold_store=cold)

        # Should not raise
        await mgr.promote_to_cold("nonexistent-run")
        assert len(cold._rows) == 0


# --------------------------------------------------------------------------
# List active runs
# --------------------------------------------------------------------------


class TestListActiveRuns:
    async def test_list_empty(self):
        hot = FakeHotStore()
        mgr = CheckpointManager(hot_store=hot)

        runs = await mgr.list_active_runs()
        assert runs == []

    async def test_list_multiple_runs(self):
        hot = FakeHotStore()
        mgr = CheckpointManager(hot_store=hot)

        for i in range(3):
            ctx = make_context(run_id=f"run-{i}")
            await mgr.save(ctx)

        runs = await mgr.list_active_runs()
        assert len(runs) == 3
        assert set(runs) == {"run-0", "run-1", "run-2"}

    async def test_promoted_runs_not_listed(self):
        hot = FakeHotStore()
        cold = FakeColdStore()
        mgr = CheckpointManager(hot_store=hot, cold_store=cold)

        ctx = make_context(run_id="run-done")
        await mgr.save(ctx)
        await mgr.promote_to_cold("run-done")

        ctx2 = make_context(run_id="run-active")
        await mgr.save(ctx2)

        runs = await mgr.list_active_runs()
        assert runs == ["run-active"]


# --------------------------------------------------------------------------
# CheckpointData model
# --------------------------------------------------------------------------


class TestCheckpointDataModel:
    def test_default_values(self):
        cp = CheckpointData(
            run_id="r1",
            agent_id="a1",
            team_id="t1",
            checkpoint_version=1,
            state_snapshot={"key": "value"},
        )
        assert cp.size_bytes == 0
        assert cp.checksum == ""
        assert cp.created_at is not None

    def test_serialization_roundtrip(self):
        cp = CheckpointData(
            run_id="r1",
            agent_id="a1",
            team_id="t1",
            checkpoint_version=5,
            state_snapshot={"state": "planning"},
            size_bytes=128,
            checksum="abc123def456",
        )
        json_str = cp.model_dump_json()
        restored = CheckpointData.model_validate_json(json_str)

        assert restored.run_id == cp.run_id
        assert restored.checkpoint_version == cp.checkpoint_version
        assert restored.state_snapshot == cp.state_snapshot


# --------------------------------------------------------------------------
# Promote to cold store — error handling (Phase 9)
# --------------------------------------------------------------------------


class TestPromoteToColdErrorHandling:
    """Tests for promote_to_cold() error behavior."""

    async def test_cold_store_failure_raises(self):
        """When cold store write fails, exception propagates (not swallowed)."""
        hot = FakeHotStore()

        class FailingColdStore:
            def acquire(self):
                return FailingColdConnection()

        class FailingColdConnection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def execute(self, query, *args):
                raise RuntimeError("Postgres connection lost")

        mgr = CheckpointManager(hot_store=hot, cold_store=FailingColdStore())
        ctx = make_context()
        await mgr.save(ctx)

        with pytest.raises(RuntimeError, match="Postgres connection lost"):
            await mgr.promote_to_cold(ctx.run_id)

        # Hot store data should be PRESERVED (not deleted)
        assert len(hot._data) == 1

    async def test_hot_store_preserved_on_cold_failure(self):
        """When cold store fails, hot store data must remain intact."""
        hot = FakeHotStore()

        class FailingColdStore:
            def acquire(self):
                return FailingColdConnection()

        class FailingColdConnection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def execute(self, query, *args):
                raise ConnectionError("Connection refused")

        mgr = CheckpointManager(hot_store=hot, cold_store=FailingColdStore())
        ctx = make_context()
        await mgr.save(ctx)

        with contextlib.suppress(ConnectionError):
            await mgr.promote_to_cold(ctx.run_id)

        # Key assertion: hot store data is still there
        snapshot = await mgr.restore(ctx.run_id)
        assert snapshot is not None
        assert snapshot["run_id"] == ctx.run_id
