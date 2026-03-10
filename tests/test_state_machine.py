"""
Tests for the custom state machine (Phase 2).

Covers:
  - Basic state transitions (happy path)
  - Guard enforcement (iteration limits, budget)
  - Invalid transition rejection
  - Rollback on side effect failure
  - The complete transition table
"""

import pytest

from agentworks.state_machine import (
    AgentState,
    ExecutionContext,
    StateMachine,
    StateTransition,
    TransitionResult,
    build_default_transition_table,
    create_agent_state_machine,
)

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def ctx() -> ExecutionContext:
    """A fresh execution context for testing."""
    return ExecutionContext(agent_id="test-agent", team_id="test-team")


@pytest.fixture
def sm() -> StateMachine:
    """A fully configured state machine."""
    return create_agent_state_machine()


# --------------------------------------------------------------------------
# Basic transition tests
# --------------------------------------------------------------------------


class TestBasicTransitions:
    async def test_idle_to_planning(self, sm: StateMachine, ctx: ExecutionContext):
        """The very first transition: IDLE -> PLANNING via 'start'."""
        assert ctx.current_state == AgentState.IDLE

        result = await sm.transition(ctx, AgentState.PLANNING, "start")

        assert result.success is True
        assert ctx.current_state == AgentState.PLANNING
        assert ctx.previous_state == AgentState.IDLE
        assert len(ctx.state_history) == 1
        assert ctx.state_history[0]["from"] == "idle"
        assert ctx.state_history[0]["to"] == "planning"

    async def test_planning_to_completed(self, sm: StateMachine, ctx: ExecutionContext):
        """Agent gets a direct answer without tool calls."""
        await sm.transition(ctx, AgentState.PLANNING, "start")

        result = await sm.transition(ctx, AgentState.COMPLETED, "has_answer")

        assert result.success is True
        assert ctx.current_state == AgentState.COMPLETED
        assert ctx.is_terminal is True

    async def test_full_tool_execution_cycle(self, sm: StateMachine, ctx: ExecutionContext):
        """Walk through a complete cycle: IDLE -> PLAN -> TOOL -> REFLECT -> PLAN -> DONE."""
        # Start
        await sm.transition(ctx, AgentState.PLANNING, "start")

        # LLM decides to call a tool
        await sm.transition(ctx, AgentState.EXECUTING_TOOL, "needs_tool")
        assert ctx.current_state == AgentState.EXECUTING_TOOL

        # Tool completes
        await sm.transition(ctx, AgentState.REFLECTING, "tool_done")
        assert ctx.current_state == AgentState.REFLECTING

        # Continue to planning
        await sm.transition(ctx, AgentState.PLANNING, "continue")
        assert ctx.current_state == AgentState.PLANNING

        # Final answer
        await sm.transition(ctx, AgentState.COMPLETED, "has_answer")
        assert ctx.current_state == AgentState.COMPLETED
        assert ctx.is_terminal is True

    async def test_awaiting_llm_flow(self, sm: StateMachine, ctx: ExecutionContext):
        """PLANNING -> AWAITING_LLM -> PLANNING (LLM response received)."""
        await sm.transition(ctx, AgentState.PLANNING, "start")

        result = await sm.transition(ctx, AgentState.AWAITING_LLM, "awaiting_llm")
        assert result.success is True
        assert ctx.current_state == AgentState.AWAITING_LLM

        result = await sm.transition(ctx, AgentState.PLANNING, "llm_responded")
        assert result.success is True
        assert ctx.current_state == AgentState.PLANNING


# --------------------------------------------------------------------------
# Guard tests
# --------------------------------------------------------------------------


class TestGuards:
    async def test_iteration_limit_blocks_tool_execution(
        self, sm: StateMachine, ctx: ExecutionContext
    ):
        """When max iterations is reached, needs_tool is blocked by guard."""
        ctx.max_iterations = 3
        ctx.iteration_count = 3  # Already at limit

        await sm.transition(ctx, AgentState.PLANNING, "start")

        result = await sm.transition(ctx, AgentState.EXECUTING_TOOL, "needs_tool")

        assert result.success is False
        assert result.guard_result is False
        assert ctx.current_state == AgentState.PLANNING  # didn't move

    async def test_iteration_limit_allows_under_limit(
        self, sm: StateMachine, ctx: ExecutionContext
    ):
        """Tool execution allowed when under iteration limit."""
        ctx.max_iterations = 10
        ctx.iteration_count = 5

        await sm.transition(ctx, AgentState.PLANNING, "start")

        result = await sm.transition(ctx, AgentState.EXECUTING_TOOL, "needs_tool")

        assert result.success is True
        assert ctx.current_state == AgentState.EXECUTING_TOOL

    async def test_budget_guard_triggers_suspension(self, sm: StateMachine, ctx: ExecutionContext):
        """When budget is exceeded, the budget_exceeded guard returns True."""
        ctx.max_budget_usd = 1.0
        ctx.token_usage.estimated_cost_usd = 1.5  # over budget

        await sm.transition(ctx, AgentState.PLANNING, "start")

        result = await sm.transition(ctx, AgentState.SUSPENDED, "budget_exceeded")
        assert result.success is True
        assert ctx.current_state == AgentState.SUSPENDED


# --------------------------------------------------------------------------
# Invalid transition tests
# --------------------------------------------------------------------------


class TestInvalidTransitions:
    async def test_cannot_go_from_idle_to_completed(self, sm: StateMachine, ctx: ExecutionContext):
        """IDLE -> COMPLETED is not in the transition table."""
        result = await sm.transition(ctx, AgentState.COMPLETED, "has_answer")

        assert result.success is False
        assert "No transition registered" in (result.error or "")
        assert ctx.current_state == AgentState.IDLE  # didn't move

    async def test_cannot_go_from_completed_to_planning(
        self, sm: StateMachine, ctx: ExecutionContext
    ):
        """Terminal states have no outbound transitions."""
        # Get to COMPLETED
        await sm.transition(ctx, AgentState.PLANNING, "start")
        await sm.transition(ctx, AgentState.COMPLETED, "has_answer")

        result = await sm.transition(ctx, AgentState.PLANNING, "continue")

        assert result.success is False
        assert ctx.current_state == AgentState.COMPLETED

    async def test_wrong_trigger_rejected(self, sm: StateMachine, ctx: ExecutionContext):
        """Correct state pair but wrong trigger is rejected."""
        await sm.transition(ctx, AgentState.PLANNING, "start")

        # PLANNING -> EXECUTING_TOOL exists, but only via "needs_tool"
        result = await sm.transition(ctx, AgentState.EXECUTING_TOOL, "wrong_trigger")

        assert result.success is False


# --------------------------------------------------------------------------
# Suspension and resume tests
# --------------------------------------------------------------------------


class TestSuspension:
    async def test_suspend_and_resume(self, sm: StateMachine, ctx: ExecutionContext):
        """A suspended run can be resumed back to PLANNING."""
        ctx.max_budget_usd = 0.50
        ctx.token_usage.estimated_cost_usd = 0.60  # over budget

        await sm.transition(ctx, AgentState.PLANNING, "start")
        await sm.transition(ctx, AgentState.SUSPENDED, "budget_exceeded")
        assert ctx.current_state == AgentState.SUSPENDED

        # Increase budget and resume
        ctx.max_budget_usd = 2.0
        result = await sm.transition(ctx, AgentState.PLANNING, "resume")
        assert result.success is True
        assert ctx.current_state == AgentState.PLANNING

    async def test_abort_suspended_run(self, sm: StateMachine, ctx: ExecutionContext):
        """A suspended run can be aborted to FAILED."""
        ctx.max_budget_usd = 0.50
        ctx.token_usage.estimated_cost_usd = 0.60

        await sm.transition(ctx, AgentState.PLANNING, "start")
        await sm.transition(ctx, AgentState.SUSPENDED, "budget_exceeded")

        result = await sm.transition(ctx, AgentState.FAILED, "abort")
        assert result.success is True
        assert ctx.current_state == AgentState.FAILED
        assert ctx.is_terminal is True


# --------------------------------------------------------------------------
# Side effects and hooks
# --------------------------------------------------------------------------


class TestSideEffectsAndHooks:
    async def test_rollback_on_side_effect_failure(self, ctx: ExecutionContext):
        """If a side effect throws, the state is rolled back."""
        sm = StateMachine()
        sm.register_transition(
            StateTransition(
                from_state=AgentState.IDLE,
                to_state=AgentState.PLANNING,
                trigger="start",
                side_effects=["explode"],
            )
        )

        async def explode(ctx: ExecutionContext, result: TransitionResult) -> None:
            raise RuntimeError("Boom!")

        sm.register_side_effect("explode", explode)

        result = await sm.transition(ctx, AgentState.PLANNING, "start")

        assert result.success is False
        assert "Boom!" in (result.error or "")
        assert ctx.current_state == AgentState.IDLE  # rolled back!

    async def test_on_enter_hook_fires(self, ctx: ExecutionContext):
        """on_enter hook is called when entering a state."""
        sm = StateMachine()
        sm.register_transition(
            StateTransition(
                from_state=AgentState.IDLE,
                to_state=AgentState.PLANNING,
                trigger="start",
            )
        )

        entered_states: list[AgentState] = []

        async def record_enter(ctx: ExecutionContext, state: AgentState) -> None:
            entered_states.append(state)

        sm.on_enter(AgentState.PLANNING, record_enter)

        await sm.transition(ctx, AgentState.PLANNING, "start")

        assert AgentState.PLANNING in entered_states


# --------------------------------------------------------------------------
# Transition table completeness tests
# --------------------------------------------------------------------------


class TestTransitionTable:
    def test_all_non_terminal_states_have_outbound_transitions(self):
        """Every non-terminal state must have at least one way out."""
        table = build_default_transition_table()
        from_states = {t.from_state for t in table}

        non_terminal = {
            AgentState.IDLE,
            AgentState.PLANNING,
            AgentState.EXECUTING_TOOL,
            AgentState.AWAITING_LLM,
            AgentState.REFLECTING,
            AgentState.SUSPENDED,
        }

        for state in non_terminal:
            assert state in from_states, (
                f"Non-terminal state {state.value} has no outbound transitions"
            )

    def test_terminal_states_have_no_outbound_transitions(self):
        """COMPLETED and FAILED are terminal — no way out."""
        table = build_default_transition_table()
        from_states = {t.from_state for t in table}

        assert AgentState.COMPLETED not in from_states
        assert AgentState.FAILED not in from_states

    def test_no_duplicate_transitions(self):
        """No two transitions share the same (from, to, trigger) triple."""
        table = build_default_transition_table()
        seen: set[tuple[str, str, str]] = set()

        for t in table:
            key = (t.from_state.value, t.to_state.value, t.trigger)
            assert key not in seen, f"Duplicate transition: {key}"
            seen.add(key)

    def test_transition_count(self):
        """Sanity check: we expect exactly 18 transitions."""
        table = build_default_transition_table()
        assert len(table) == 18


# --------------------------------------------------------------------------
# ExecutionContext tests
# --------------------------------------------------------------------------


class TestExecutionContext:
    def test_serialization_roundtrip(self):
        """Context survives JSON serialization (critical for checkpointing)."""
        ctx = ExecutionContext(
            agent_id="test",
            team_id="team-a",
            current_state=AgentState.PLANNING,
            iteration_count=5,
        )

        json_str = ctx.model_dump_json()
        restored = ExecutionContext.model_validate_json(json_str)

        assert restored.agent_id == "test"
        assert restored.current_state == AgentState.PLANNING
        assert restored.iteration_count == 5

    def test_budget_remaining(self):
        ctx = ExecutionContext(agent_id="test", max_budget_usd=2.0)
        ctx.token_usage.estimated_cost_usd = 0.75
        assert ctx.budget_remaining_usd == pytest.approx(1.25)

    def test_is_terminal(self):
        ctx = ExecutionContext(agent_id="test")
        assert ctx.is_terminal is False

        ctx.current_state = AgentState.COMPLETED
        assert ctx.is_terminal is True

        ctx.current_state = AgentState.FAILED
        assert ctx.is_terminal is True

    def test_validation_rejects_bad_iterations(self):
        with pytest.raises(ValueError, match="max_iterations must be between"):
            ExecutionContext(agent_id="test", max_iterations=0)

    def test_validation_rejects_bad_budget(self):
        with pytest.raises(ValueError, match="max_budget_usd must be between"):
            ExecutionContext(agent_id="test", max_budget_usd=-1.0)

    def test_token_usage_addition(self):
        usage = ExecutionContext(agent_id="test").token_usage
        usage.add(prompt=1000, completion=500, cost_per_1k_input=0.01, cost_per_1k_output=0.03)

        assert usage.prompt_tokens == 1000
        assert usage.completion_tokens == 500
        assert usage.total_tokens == 1500
        assert usage.estimated_cost_usd == pytest.approx(0.01 + 0.015)
