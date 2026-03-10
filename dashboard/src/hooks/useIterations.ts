import { useMemo } from 'react'
import type { RunData, Iteration, IterationToolCall, Message, ToolCallRecord, ErrorTier } from '../api/types'

/**
 * Classify a tool call into an error tier based on its result data.
 *
 * - RETRYABLE: retry_count > 0 but ultimately succeeded (transparent to agent)
 * - RECOVERABLE: output_data contains error_type (API returned error content, agent can self-correct)
 * - FATAL: ToolCallRecord.error is set (hard failure)
 */
function classifyErrorTier(tc: ToolCallRecord): ErrorTier {
  if (tc.error) return 'fatal'
  const out = tc.output_data as Record<string, unknown> | null
  if (out?.error_type || out?.error) return 'recoverable'
  if (tc.retry_count > 0) return 'retryable'
  return null
}

function toIterationToolCall(tc: ToolCallRecord): IterationToolCall {
  return {
    name: tc.tool_name,
    input: tc.input_data,
    output: tc.output_data,
    error: tc.error,
    duration_ms: tc.duration_ms,
    retry_count: tc.retry_count,
    error_tier: classifyErrorTier(tc),
  }
}

/**
 * Groups flat API data (messages, tool_calls, state_history) into
 * Iteration objects by walking state transitions sequentially and
 * consuming messages/tool_calls in order.
 *
 * An iteration is one cycle of: PLANNING -> EXECUTING_TOOL -> REFLECTING
 */
export function useIterations(run: RunData | null): Iteration[] {
  return useMemo(() => {
    if (!run) return []

    const { messages, tool_calls, state_history } = run

    // Build sorted assistant messages queue (consumed in order)
    const assistantMsgs = messages
      .filter(m => m.role === 'assistant')
      .slice() // don't mutate original

    // Build tool call queue (consumed in order)
    const toolQueue = [...tool_calls]

    let msgIdx = 0
    let tcIdx = 0

    const iterations: Iteration[] = []
    let current: PartialIteration | null = null
    let iterIndex = 0

    for (const transition of state_history) {
      // Start a new iteration only on genuine boundaries:
      // - idle → planning (first iteration)
      // - reflecting → planning (new cycle after observing tool result)
      // NOT on awaiting_llm → planning (that's the LLM responding within the same cycle)
      const isNewIteration = transition.to === 'planning' && transition.from !== 'awaiting_llm'

      if (isNewIteration) {
        if (current) {
          iterations.push(finalize(current))
        }
        current = { index: iterIndex++ }
      }

      if (!current) {
        current = { index: iterIndex++ }
      }

      // Consume assistant message when LLM responds (awaiting_llm → planning).
      // This overwrites the placeholder set by the iteration start.
      if (transition.to === 'planning' && transition.from === 'awaiting_llm') {
        const msg = consumeNextAssistant(assistantMsgs, msgIdx)
        if (msg) {
          msgIdx = msg.nextIdx
          current.planning = {
            content: msg.message.content,
            tokens: estimateTokens(msg.message.content),
            cost: 0,
            timestamp: transition.timestamp,
          }
        } else if (!current.planning) {
          current.planning = {
            content: null,
            tokens: 0,
            cost: 0,
            timestamp: transition.timestamp,
          }
        }
      }

      // Set planning placeholder on iteration start (before LLM responds)
      if (isNewIteration && !current.planning) {
        current.planning = {
          content: null,
          tokens: 0,
          cost: 0,
          timestamp: transition.timestamp,
        }
      }

      // Tool execution: consume next tool call
      if (transition.to === 'executing_tool') {
        if (!current.toolCall && tcIdx < toolQueue.length) {
          const tc = toolQueue[tcIdx++]
          current.toolCall = toIterationToolCall(tc)

          // Collect additional parallel tool calls (same timestamp or consecutive executing_tool)
          const additionalCalls: NonNullable<Iteration['additionalToolCalls']> = []
          while (tcIdx < toolQueue.length) {
            const next = toolQueue[tcIdx]
            // If the next tool call started within 1 second, treat as parallel
            const gap = Math.abs(
              new Date(next.started_at).getTime() - new Date(tc.started_at).getTime()
            )
            if (gap < 1000) {
              additionalCalls.push(toIterationToolCall(next))
              tcIdx++
            } else {
              break
            }
          }
          if (additionalCalls.length > 0) {
            current.additionalToolCalls = additionalCalls
          }
        }
      }

      // Reflection phase: only consume if the next message has actual content.
      // Null-content messages are tool_call placeholders → leave for next planning.
      if (transition.to === 'reflecting') {
        if (msgIdx < assistantMsgs.length && assistantMsgs[msgIdx].content) {
          const msg = assistantMsgs[msgIdx++]
          current.reflection = {
            content: msg.content,
            tokens: estimateTokens(msg.content),
            cost: 0,
            timestamp: transition.timestamp,
          }
        } else {
          current.reflection = {
            content: null,
            tokens: 0,
            cost: 0,
            timestamp: transition.timestamp,
          }
        }
      }

      // Completed: scan remaining messages for one with content as final reflection.
      if (transition.to === 'completed') {
        for (let i = msgIdx; i < assistantMsgs.length; i++) {
          if (assistantMsgs[i].content) {
            msgIdx = i + 1
            current.reflection = {
              content: assistantMsgs[i].content,
              tokens: estimateTokens(assistantMsgs[i].content),
              cost: 0,
              timestamp: transition.timestamp,
            }
            break
          }
        }
      }
    }

    // Push last iteration (skip if completely empty — no tool, no content)
    if (current) {
      const hasContent = current.planning?.content || current.toolCall || current.reflection?.content
      if (hasContent || iterations.length === 0) {
        iterations.push(finalize(current))
      }
    }

    // If no state_history but we have messages, create a single iteration
    if (iterations.length === 0 && messages.length > 0) {
      const assistantContent = messages
        .filter(m => m.role === 'assistant' && m.content)
        .map(m => m.content)
        .join('\n\n')

      iterations.push({
        index: 0,
        planning: assistantContent ? {
          content: assistantContent,
          tokens: estimateTokens(assistantContent),
          cost: run.total_cost_usd,
          timestamp: messages[0]?.timestamp ?? '',
        } : null,
        toolCall: tool_calls.length > 0 ? toIterationToolCall(tool_calls[0]) : null,
        reflection: null,
        totalCost: run.total_cost_usd,
        totalTokens: run.total_tokens,
        duration_ms: run.duration_ms ?? 0,
      })
    }

    // Distribute run-level tokens across iterations if per-iteration data is empty
    if (iterations.length > 0 && run.total_tokens > 0) {
      const hasPerIterTokens = iterations.some(i => i.totalTokens > 0)
      if (!hasPerIterTokens) {
        const perIter = Math.round(run.total_tokens / iterations.length)
        for (const iter of iterations) {
          iter.totalTokens = perIter
        }
      }
    }

    return iterations
  }, [run])
}

type PartialIteration = Partial<Iteration> & { index: number; additionalToolCalls?: Iteration['additionalToolCalls'] }

function finalize(partial: PartialIteration): Iteration {
  return {
    index: partial.index,
    planning: partial.planning ?? null,
    toolCall: partial.toolCall ?? null,
    reflection: partial.reflection ?? null,
    additionalToolCalls: partial.additionalToolCalls,
    totalCost: (partial.planning?.cost ?? 0) + (partial.reflection?.cost ?? 0),
    totalTokens: (partial.planning?.tokens ?? 0) + (partial.reflection?.tokens ?? 0),
    duration_ms: 0,
  }
}

/**
 * Consume the next assistant message strictly in order.
 * The array is pre-filtered to assistant-only, so just take the next one.
 */
function consumeNextAssistant(
  messages: Message[],
  fromIdx: number,
): { message: Message; nextIdx: number } | null {
  if (fromIdx >= messages.length) return null
  return { message: messages[fromIdx], nextIdx: fromIdx + 1 }
}

function estimateTokens(content: string | null): number {
  if (!content) return 0
  return Math.max(1, Math.ceil(content.length / 4))
}
