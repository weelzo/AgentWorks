import { useState } from 'react'
import type { Iteration, IterationToolCall, ErrorTier } from '../../api/types'
import { formatCost, formatTokens, formatTimestamp } from '../../utils/format'

function ErrorTierBadge({ tier }: { tier: ErrorTier }) {
  if (!tier) return null
  const config = {
    retryable: { label: 'RETRYABLE', sub: 'auto-resolved', bg: 'bg-ctp-peach/15', border: 'border-ctp-peach/30', text: 'text-ctp-peach', icon: 'autorenew' },
    recoverable: { label: 'RECOVERABLE', sub: 'agent self-corrected', bg: 'bg-ctp-yellow/15', border: 'border-ctp-yellow/30', text: 'text-ctp-yellow', icon: 'healing' },
    fatal: { label: 'FATAL', sub: 'run failed', bg: 'bg-ctp-red/15', border: 'border-ctp-red/30', text: 'text-ctp-red', icon: 'dangerous' },
  }[tier]
  return (
    <div className={`${config.bg} border ${config.border} rounded-lg px-3 py-1.5 flex items-center gap-2`}>
      <span className={`material-symbols-outlined text-sm ${config.text}`}>{config.icon}</span>
      <div>
        <span className={`text-[10px] font-black uppercase tracking-wider ${config.text}`}>{config.label}</span>
        <span className="text-[10px] text-ctp-subtext0 ml-1.5">{config.sub}</span>
      </div>
    </div>
  )
}

function RetryBadge({ count }: { count: number }) {
  if (count <= 0) return null
  return (
    <div className="bg-ctp-peach/15 border border-ctp-peach/30 rounded px-2 py-0.5 flex items-center gap-1">
      <span className="material-symbols-outlined text-ctp-peach text-xs">replay</span>
      <span className="text-[10px] font-bold text-ctp-peach">retried {count}x</span>
    </div>
  )
}

function hasToolError(tc: IterationToolCall | null | undefined): boolean {
  if (!tc) return false
  if (tc.error) return true
  if (tc.error_tier === 'recoverable' || tc.error_tier === 'fatal') return true
  return false
}

export function IterationCard({
  iteration,
  defaultExpanded = false,
  isFailed = false,
}: {
  iteration: Iteration
  defaultExpanded?: boolean
  isFailed?: boolean
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const hasError = hasToolError(iteration.toolCall) ||
    (iteration.additionalToolCalls?.some(tc => hasToolError(tc)) ?? false)
  const hasRetry = (iteration.toolCall?.retry_count ?? 0) > 0 ||
    (iteration.additionalToolCalls?.some(tc => tc.retry_count > 0) ?? false)
  // Determine highest error tier for color: fatal=red, recoverable=yellow, retryable=peach
  const allToolCalls = [iteration.toolCall, ...(iteration.additionalToolCalls ?? [])].filter(Boolean) as IterationToolCall[]
  const hasFatal = isFailed || allToolCalls.some(tc => tc.error_tier === 'fatal' || tc.error)
  const hasRecoverable = allToolCalls.some(tc => tc.error_tier === 'recoverable')
  const borderColor = hasFatal
    ? 'bg-ctp-red'
    : hasRecoverable ? 'bg-ctp-yellow' : hasRetry ? 'bg-ctp-peach' : 'bg-ctp-green'

  return (
    <div className="relative pl-8 pb-8">
      {/* Vertical line */}
      <div className={`absolute left-0 top-0 bottom-0 w-1 ${borderColor} rounded-full ${
        hasFatal ? 'shadow-[0_0_10px_rgba(243,139,168,0.3)]' : hasRecoverable ? 'shadow-[0_0_10px_rgba(249,226,175,0.3)]' : !hasRetry ? 'shadow-[0_0_10px_rgba(166,227,161,0.3)]' : 'shadow-[0_0_10px_rgba(250,179,135,0.3)]'
      }`} />

      {/* Iteration header */}
      <div
        className="flex items-center justify-between mb-4 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-3">
          <div className={`${
            hasFatal ? 'bg-ctp-red text-ctp-crust' : hasRecoverable ? 'bg-ctp-yellow text-ctp-crust' : hasRetry ? 'bg-ctp-peach text-ctp-crust' : 'bg-ctp-green text-ctp-crust'
          } text-[10px] font-black px-2 py-0.5 rounded uppercase`}>
            Iteration {iteration.index + 1}
          </div>
          {expanded ? null : (
            <span className="material-symbols-outlined text-ctp-subtext0 text-sm">expand_more</span>
          )}
        </div>
        <div className="flex gap-4 text-xs text-ctp-subtext0 font-mono items-center">
          {iteration.totalCost > 0 && (
            <div className="flex items-center gap-1">
              <span className="material-symbols-outlined text-xs">monetization_on</span>
              {formatCost(iteration.totalCost)}
            </div>
          )}
          {iteration.totalTokens > 0 && (
            <span>{formatTokens(iteration.totalTokens)} tokens</span>
          )}
          {iteration.planning?.timestamp && (
            <>
              <div className="h-3 w-px bg-ctp-surface0" />
              <span className="text-ctp-subtext0 font-bold">{formatTimestamp(iteration.planning.timestamp)}</span>
            </>
          )}
        </div>
      </div>

      {/* Expanded steps */}
      {expanded && (
        <div className="flex flex-col gap-4">
          {/* Planning step */}
          {iteration.planning && (
            <div className="bg-ctp-surface0/40 border border-ctp-surface0 p-4 rounded-xl">
              <div className="flex items-start gap-4">
                <div className="size-8 rounded-lg bg-ctp-blue/20 text-ctp-blue flex items-center justify-center shrink-0">
                  <span className="material-symbols-outlined text-xl">psychology</span>
                </div>
                <div className="flex-1 min-w-0">
                  <h4 className="text-sm font-bold text-white mb-1">Planning Phase</h4>
                  {iteration.planning.content ? (
                    <p className="text-sm text-ctp-subtext0 leading-relaxed whitespace-pre-wrap">{iteration.planning.content}</p>
                  ) : (
                    <p className="text-sm text-ctp-overlay0 italic">
                      {iteration.toolCall ? `Agent selected tool: ${iteration.toolCall.name}` : 'Agent reasoning not captured'}
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Tool call step */}
          {iteration.toolCall && (
            <ToolCallCard tc={iteration.toolCall} />
          )}

          {/* Additional tool calls (parallel) */}
          {iteration.additionalToolCalls?.map((tc, i) => (
            <ToolCallCard key={i} tc={tc} />
          ))}

          {/* Reflection step */}
          {iteration.reflection && (
            <div className={`bg-ctp-surface0/40 border p-4 rounded-xl ${
              hasFatal ? 'border-ctp-red/30' : hasRecoverable ? 'border-ctp-yellow/30' : 'border-ctp-surface0'
            }`}>
              <div className="flex items-start gap-4">
                <div className={`size-8 rounded-lg flex items-center justify-center shrink-0 ${
                  hasError ? 'bg-ctp-yellow/20 text-ctp-yellow' : 'bg-ctp-green/20 text-ctp-green'
                }`}>
                  <span className="material-symbols-outlined text-xl">
                    {hasError ? 'psychology_alt' : 'chat_bubble'}
                  </span>
                </div>
                <div className="flex-1 min-w-0">
                  <h4 className="text-sm font-bold text-white mb-1">
                    {hasError ? 'Error Observed — Agent Reflecting' : 'Reflection & Reasoning'}
                  </h4>
                  {iteration.reflection.content ? (
                    <p className="text-sm text-ctp-subtext0 leading-relaxed italic whitespace-pre-wrap">{iteration.reflection.content}</p>
                  ) : (
                    <p className="text-sm text-ctp-overlay0 italic">
                      {(hasFatal || hasRecoverable)
                        ? 'Agent observed tool error and will attempt self-correction in the next iteration'
                        : 'Agent processed tool results'}
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ToolCallCard({ tc }: { tc: IterationToolCall }) {
  const isError = hasToolError(tc)
  const isRecoverable = tc.error_tier === 'recoverable'
  const outputData = tc.output as Record<string, unknown> | null

  // For recoverable errors, extract the error message from output_data
  const recoverableError = isRecoverable && outputData?.error
    ? String(outputData.error)
    : null

  const isFatal = tc.error_tier === 'fatal' || (tc.error && tc.error_tier !== 'recoverable')
  const headerBg = isFatal ? 'bg-ctp-red/10' : isRecoverable ? 'bg-ctp-yellow/10' : tc.retry_count > 0 ? 'bg-ctp-peach/10' : 'bg-ctp-yellow/10'
  const accentColor = isFatal ? 'text-ctp-red' : isRecoverable ? 'text-ctp-yellow' : 'text-ctp-yellow'

  return (
    <div className={`bg-ctp-surface0/40 border rounded-xl overflow-hidden ${
      isFatal ? 'border-ctp-red/30' : isRecoverable ? 'border-ctp-yellow/30' : tc.retry_count > 0 ? 'border-ctp-peach/30' : 'border-ctp-surface0'
    }`}>
      <div className={`flex items-center justify-between px-4 py-2 ${headerBg} border-b border-ctp-surface0`}>
        <div className="flex items-center gap-2">
          <span className={`material-symbols-outlined text-sm ${isError ? accentColor : 'text-ctp-yellow'}`}>
            {isError ? 'error' : 'database'}
          </span>
          <span className={`text-xs font-bold font-mono ${isError ? accentColor : 'text-ctp-yellow'}`}>
            tool:{tc.name}
          </span>
          <RetryBadge count={tc.retry_count} />
        </div>
        {tc.duration_ms != null && (
          <span className="text-[10px] text-ctp-subtext0">latency: {tc.duration_ms.toFixed(0)}ms</span>
        )}
      </div>

      {/* Error tier badge */}
      {tc.error_tier && (
        <div className="px-4 pt-3">
          <ErrorTierBadge tier={tc.error_tier} />
        </div>
      )}

      <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <p className="text-[10px] text-ctp-subtext0 uppercase font-bold mb-2">Input</p>
          <pre className="bg-ctp-crust p-3 rounded-lg font-mono text-[11px] text-ctp-blue overflow-x-auto">
            {JSON.stringify(tc.input, null, 2)}
          </pre>
        </div>
        <div>
          <p className="text-[10px] text-ctp-subtext0 uppercase font-bold mb-2">
            {tc.error ? 'Error' : recoverableError ? 'Error Response' : 'Output'}
          </p>
          {tc.error ? (
            <div className="bg-ctp-red/10 border border-ctp-red/20 p-3 rounded-lg text-[11px] text-ctp-red">
              {tc.error}
            </div>
          ) : recoverableError ? (
            <div className="bg-ctp-yellow/10 border border-ctp-yellow/20 p-3 rounded-lg text-[11px] text-ctp-yellow">
              {recoverableError}
            </div>
          ) : (
            <pre className="bg-ctp-crust p-3 rounded-lg font-mono text-[11px] text-ctp-green overflow-x-auto">
              {JSON.stringify(tc.output, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </div>
  )
}
