"""
Phase 4: Execution Engine

The main execution loop integrating:
  - State machine (Phase 2) for lifecycle management
  - Tool registry (Phase 3) for tool discovery and execution
  - Error classifier for 3-tier error handling
  - Checkpoint manager for state persistence

Every agent run goes through this loop:
  IDLE → PLANNING → (EXECUTING_TOOL → REFLECTING)* → COMPLETED

The engine's job is to:
  1. Drive the state machine through transitions
  2. Call the LLM when in PLANNING state
  3. Execute tools when in EXECUTING_TOOL state
  4. Let the LLM evaluate tool results in REFLECTING state
  5. Checkpoint state at every transition
  6. Classify and handle errors at every step
  7. Enforce budget and iteration limits
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agentworks.errors import ErrorClassifier, ErrorTier
from agentworks.state_machine import (
    AgentState,
    ExecutionContext,
    Message,
    StateMachine,
    ToolCallRecord,
)

if TYPE_CHECKING:
    from agentworks.checkpoint import CheckpointManager
    from agentworks.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """
    The main agent execution engine.

    Orchestrates the agent loop with error handling, checkpointing,
    and budget enforcement at every step.
    """

    def __init__(
        self,
        state_machine: StateMachine,
        tool_registry: ToolRegistry,
        checkpoint_mgr: CheckpointManager,
        llm_gateway: Any,  # defined in Phase 5
        error_classifier: ErrorClassifier | None = None,
    ) -> None:
        self._sm = state_machine
        self._tools = tool_registry
        self._checkpoints = checkpoint_mgr
        self._llm = llm_gateway
        self._errors = error_classifier or ErrorClassifier()

    async def run(self, ctx: ExecutionContext) -> ExecutionContext:
        """
        Execute an agent run to completion.

        This is the top-level entry point. It manages the full lifecycle:
          1. Start the run (IDLE → PLANNING)
          2. Enter the main loop
          3. On completion: promote checkpoint to cold store
          4. On failure: record error, promote checkpoint
          5. Return the final context
        """
        run_start = time.monotonic()
        logger.info(
            "Starting run %s for agent %s (team: %s)",
            ctx.run_id,
            ctx.agent_id,
            ctx.team_id,
        )

        try:
            # Transition: IDLE → PLANNING (skip if already in PLANNING, e.g. from resume)
            if ctx.current_state == AgentState.IDLE:
                result = await self._sm.transition(ctx, AgentState.PLANNING, "start")
                if not result.success:
                    raise RuntimeError(f"Failed to start run: {result.error}")
            elif ctx.current_state != AgentState.PLANNING:
                raise RuntimeError(f"Cannot start run from state: {ctx.current_state.value}")

            # Main execution loop
            # SUSPENDED is not terminal (it can be resumed), but the
            # engine must stop looping — resume() starts a new run().
            while not ctx.is_terminal and ctx.current_state != AgentState.SUSPENDED:  # type: ignore[comparison-overlap]
                await self._execute_step(ctx)

            # Record completion time
            ctx.completed_at = datetime.now(UTC)
            total_ms = (time.monotonic() - run_start) * 1000

            logger.info(
                "Run %s finished: state=%s iterations=%d cost=$%.4f duration=%.0fms",
                ctx.run_id,
                ctx.current_state.value,
                ctx.iteration_count,
                ctx.token_usage.estimated_cost_usd,
                total_ms,
            )

        except Exception as e:
            logger.error("Run %s crashed: %s", ctx.run_id, e, exc_info=True)
            ctx.last_error = str(e)
            ctx.error_history.append(
                {
                    "error": str(e),
                    "timestamp": datetime.now(UTC).isoformat(),
                    "state": ctx.current_state.value,
                }
            )
            # Force transition to FAILED
            await self._sm.transition(ctx, AgentState.FAILED, "error")

        finally:
            # Always promote to cold store on terminal state
            if ctx.is_terminal:
                try:
                    await self._checkpoints.promote_to_cold(ctx.run_id)
                except Exception as promote_err:
                    logger.error(
                        "Failed to promote run %s to cold store: %s (hot store data preserved)",
                        ctx.run_id,
                        promote_err,
                    )

        return ctx

    async def resume(self, run_id: str) -> ExecutionContext:
        """
        Resume a suspended or crashed run from its last checkpoint.

        Used for:
          - Runs in SUSPENDED state (budget increase, manual approval)
          - Runs that crashed mid-execution (process restart)
        """
        state_snapshot = await self._checkpoints.restore(run_id)
        if state_snapshot is None:
            raise ValueError(f"No checkpoint found for run {run_id}")

        ctx = ExecutionContext.model_validate(state_snapshot)
        logger.info(
            "Resuming run %s from state %s (checkpoint v%d)",
            ctx.run_id,
            ctx.current_state.value,
            ctx.checkpoint_version,
        )

        if ctx.current_state == AgentState.SUSPENDED:
            await self._sm.transition(ctx, AgentState.PLANNING, "resume")

        return await self.run(ctx)

    async def _execute_step(self, ctx: ExecutionContext) -> None:
        """
        Execute a single step of the agent loop.

        The current state determines what happens:
          PLANNING: Call LLM to decide next action
          EXECUTING_TOOL: Execute the selected tool
          REFLECTING: Evaluate tool result, decide to continue or finish
        """
        if ctx.current_state == AgentState.PLANNING:
            await self._step_planning(ctx)
        elif ctx.current_state == AgentState.EXECUTING_TOOL:
            await self._step_execute_tool(ctx)
        elif ctx.current_state == AgentState.REFLECTING:
            await self._step_reflecting(ctx)
        elif ctx.current_state == AgentState.SUSPENDED:
            logger.warning(
                "Run %s is suspended. Call resume() to continue.",
                ctx.run_id,
            )
        else:
            raise RuntimeError(f"Unexpected state in execution loop: {ctx.current_state}")

    async def _step_planning(self, ctx: ExecutionContext) -> None:
        """
        Planning step: Ask the LLM what to do next.

        The LLM receives conversation history + tool descriptions and
        responds with either a tool call or a text answer.
        """
        # Budget check before calling LLM
        if ctx.budget_remaining_usd <= 0:
            await self._sm.transition(ctx, AgentState.SUSPENDED, "budget_exceeded")
            return

        # Iteration check: hard fail unless we're in the grace period
        # (_iteration_grace is set by _step_reflecting for one final LLM call)
        if ctx.iterations_remaining <= 0 and "_iteration_grace" not in ctx.metadata:
            ctx.last_error = f"Max iterations reached ({ctx.max_iterations})"
            await self._sm.transition(ctx, AgentState.FAILED, "error")
            return

        # Get available tool specs (scoped to run's tool_ids if specified)
        tool_specs = self._tools.get_llm_tool_specs(tool_ids=ctx.tool_ids)

        try:
            # Transition to AWAITING_LLM
            await self._sm.transition(ctx, AgentState.AWAITING_LLM, "awaiting_llm")

            # Call LLM
            llm_response = await self._llm.complete(
                messages=[m.model_dump(mode="json") for m in ctx.messages],
                tools=tool_specs,
                metadata={
                    "run_id": ctx.run_id,
                    "team_id": ctx.team_id,
                    "iteration": ctx.iteration_count,
                },
            )

            # Track token usage
            if hasattr(llm_response, "usage") and hasattr(llm_response, "model_cost"):
                ctx.token_usage.add(
                    prompt=llm_response.usage.prompt_tokens,
                    completion=llm_response.usage.completion_tokens,
                    cost_per_1k_input=llm_response.model_cost.input_per_1k,
                    cost_per_1k_output=llm_response.model_cost.output_per_1k,
                )

            # Transition back to PLANNING (LLM responded)
            await self._sm.transition(ctx, AgentState.PLANNING, "llm_responded")

            # Process LLM response
            tool_calls = getattr(llm_response, "tool_calls", None) or []
            content = getattr(llm_response, "content", None)

            if tool_calls:
                # LLM wants to call tool(s) — may be parallel
                ctx.messages.append(
                    Message(
                        role="assistant",
                        content=content,
                        tool_calls=[
                            tc.model_dump() if hasattr(tc, "model_dump") else tc
                            for tc in tool_calls
                        ],
                    )
                )

                # Build queue of all tool calls
                def _extract_tc(tc: Any) -> dict[str, Any]:
                    name = tc.name if hasattr(tc, "name") else tc.get("name", "")
                    args = tc.arguments if hasattr(tc, "arguments") else tc.get("arguments", {})
                    tid = tc.id if hasattr(tc, "id") else tc.get("id", "")
                    return {"tool_id": name, "input_data": args, "call_id": tid}

                pending_queue = [_extract_tc(tc) for tc in tool_calls]
                ctx.metadata["pending_tool_call"] = pending_queue[0]
                ctx.metadata["pending_tool_queue"] = pending_queue[1:]

                # Clear grace flag — the LLM had its chance
                ctx.metadata.pop("_iteration_grace", None)

                tr = await self._sm.transition(ctx, AgentState.EXECUTING_TOOL, "needs_tool")
                if not tr.success:
                    # Guard rejected (e.g., iteration limit). Fail gracefully.
                    ctx.metadata.pop("pending_tool_call", None)
                    ctx.metadata.pop("pending_tool_queue", None)
                    ctx.last_error = f"Max iterations reached ({ctx.max_iterations})"
                    await self._sm.transition(ctx, AgentState.FAILED, "error")
            else:
                # LLM has a final answer
                ctx.metadata.pop("_iteration_grace", None)
                ctx.messages.append(
                    Message(
                        role="assistant",
                        content=content,
                    )
                )
                await self._sm.transition(ctx, AgentState.COMPLETED, "has_answer")

        except Exception as e:
            classified = self._errors.classify(
                error_type=type(e).__name__,
                message=str(e),
            )

            if classified.tier == ErrorTier.FATAL:
                ctx.last_error = classified.message
                ctx.error_history.append(
                    {
                        "error": classified.message,
                        "timestamp": datetime.now(UTC).isoformat(),
                        "state": ctx.current_state.value,
                        "tier": "fatal",
                    }
                )
                # Might be in AWAITING_LLM — need to handle both states
                if ctx.current_state == AgentState.AWAITING_LLM:
                    await self._sm.transition(ctx, AgentState.FAILED, "llm_error")
                else:
                    await self._sm.transition(ctx, AgentState.FAILED, "error")
            elif classified.tier == ErrorTier.RETRYABLE:
                ctx.last_error = f"LLM call failed after retries: {classified.message}"
                ctx.error_history.append(
                    {
                        "error": classified.message,
                        "timestamp": datetime.now(UTC).isoformat(),
                        "state": ctx.current_state.value,
                        "tier": "retryable",
                    }
                )
                if ctx.current_state == AgentState.AWAITING_LLM:
                    await self._sm.transition(ctx, AgentState.FAILED, "llm_error")
                else:
                    await self._sm.transition(ctx, AgentState.FAILED, "error")
            else:
                # Recoverable: add error context and try again
                ctx.messages.append(
                    Message(
                        role="system",
                        content=(
                            f"Error occurred: {classified.message}. {classified.recovery_hint}"
                        ),
                    )
                )
                ctx.iteration_count += 1
                # If we're in AWAITING_LLM, transition back to PLANNING
                if ctx.current_state == AgentState.AWAITING_LLM:
                    await self._sm.transition(ctx, AgentState.PLANNING, "llm_responded")

    async def _step_execute_tool(self, ctx: ExecutionContext) -> None:
        """
        Execute all pending tool calls and transition to REFLECTING.

        When the LLM returns multiple tool calls (parallel), we execute
        them all sequentially and add each result to messages before
        moving on. OpenAI requires a tool result for every tool_call_id.
        """
        # Build the full list: current pending + any queued
        pending = ctx.metadata.get("pending_tool_call")
        if not pending:
            logger.error(
                "Run %s: in EXECUTING_TOOL but no pending tool call",
                ctx.run_id,
            )
            await self._sm.transition(ctx, AgentState.FAILED, "fatal_error")
            return

        all_calls = [pending] + ctx.metadata.get("pending_tool_queue", [])
        any_fatal = False
        last_had_error = False

        for call_info in all_calls:
            tool_id = call_info["tool_id"]
            input_data = call_info["input_data"]
            call_id = call_info["call_id"]

            record = ToolCallRecord(
                tool_call_id=call_id,
                tool_name=tool_id,
                input_data=input_data,
            )

            result = await self._tools.execute(tool_id, input_data, ctx)

            record.completed_at = datetime.now(UTC)
            record.duration_ms = result.latency_ms
            record.retry_count = result.retry_count

            if result.success:
                record.output_data = result.output
                ctx.tool_calls.append(record)
                ctx.messages.append(
                    Message(
                        role="tool",
                        tool_call_id=call_id,
                        content=json.dumps(result.output),
                        name=tool_id,
                    )
                )
            else:
                record.error = result.error
                ctx.tool_calls.append(record)
                last_had_error = True

                classified = self._errors.classify(
                    error_type=result.error_type or "unknown",
                    message=result.error or "Unknown error",
                    tool_id=tool_id,
                )

                if classified.tier == ErrorTier.FATAL:
                    any_fatal = True
                    ctx.last_error = classified.message

                # Always add a tool result message (OpenAI requires it)
                ctx.messages.append(
                    Message(
                        role="tool",
                        tool_call_id=call_id,
                        content=json.dumps(
                            {
                                "error": classified.message,
                                "hint": classified.recovery_hint,
                            }
                        ),
                        name=tool_id,
                    )
                )

        # Clean up metadata
        ctx.metadata.pop("pending_tool_call", None)
        ctx.metadata.pop("pending_tool_queue", None)

        if any_fatal:
            await self._sm.transition(ctx, AgentState.FAILED, "fatal_error")
        else:
            trigger = "tool_error" if last_had_error else "tool_done"
            await self._sm.transition(ctx, AgentState.REFLECTING, trigger)

    async def _step_reflecting(self, ctx: ExecutionContext) -> None:
        """
        Reflection step: After tool execution, decide what to do next.

        Increments the iteration counter and transitions back to PLANNING
        so the LLM can see the tool result and decide the next action.
        """
        ctx.iteration_count += 1

        # Budget check
        if ctx.budget_remaining_usd <= 0:
            await self._sm.transition(ctx, AgentState.SUSPENDED, "budget_exceeded")
            return

        # Iteration check — graceful degradation instead of hard failure
        if ctx.iterations_remaining <= 0:
            ctx.messages.append(
                Message(
                    role="system",
                    content=(
                        "You have reached the maximum number of iterations. "
                        "Please provide your best answer based on the "
                        "information gathered so far."
                    ),
                )
            )
            # Set grace flag so _step_planning allows one final LLM call.
            # The guard on PLANNING → EXECUTING_TOOL prevents new tool calls.
            ctx.metadata["_iteration_grace"] = True

        await self._sm.transition(ctx, AgentState.PLANNING, "continue")
