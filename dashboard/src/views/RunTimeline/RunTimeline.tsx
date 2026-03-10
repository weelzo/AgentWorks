import { useEffect } from 'react'
import { useSearchParams } from 'react-router'
import { useRunStore } from '../../store/runStore'
import { useIterations } from '../../hooks/useIterations'
import { Badge } from '../../components/Badge'
import { formatCost, formatTokens, formatDuration } from '../../utils/format'
import { IterationCard } from './IterationCard'
import { TimelineConnector } from './TimelineConnector'
import type { AgentState } from '../../api/types'

export function RunTimeline() {
  const [searchParams] = useSearchParams()
  const runId = searchParams.get('run')
  const { currentRun, loading, error, loadRun } = useRunStore()
  const iterations = useIterations(currentRun)

  useEffect(() => {
    if (runId) loadRun(runId)
  }, [runId, loadRun])

  if (!runId) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-ctp-subtext0 gap-3">
        <span className="material-symbols-outlined text-6xl text-ctp-surface1">timeline</span>
        <p className="text-sm">Enter a run ID to view its timeline</p>
        <p className="text-xs text-ctp-overlay0">Press <kbd className="px-1.5 py-0.5 bg-ctp-surface0 rounded text-ctp-text text-[10px]">/</kbd> to focus the search</p>
      </div>
    )
  }
  if (loading) {
    return (
      <div className="p-6 space-y-4">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="h-20 bg-ctp-surface0 rounded-xl animate-pulse" />
        ))}
      </div>
    )
  }
  if (error) return <div className="p-8 text-ctp-red">{error}</div>
  if (!currentRun) return null

  const state = currentRun.state as AgentState
  const isFailed = state === 'failed'

  // Compute error recovery stats
  const retryCount = currentRun.tool_calls.filter(tc => tc.retry_count > 0).length
  const recoverableCount = currentRun.tool_calls.filter(tc => {
    const out = tc.output_data as Record<string, unknown> | null
    return out?.error_type || out?.error
  }).length
  const fatalCount = currentRun.tool_calls.filter(tc => tc.error != null).length
  const totalErrors = retryCount + recoverableCount + fatalCount
  const totalRetries = currentRun.tool_calls.reduce((sum, tc) => sum + tc.retry_count, 0)

  return (
    <div className="flex-1 overflow-y-auto p-6">
      {/* Top Summary Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3 mb-8">
        <div className="bg-ctp-base border border-ctp-surface0 p-3 rounded-xl">
          <p className="text-[10px] text-ctp-subtext0 uppercase font-bold tracking-wider mb-1">Status</p>
          <Badge state={state} />
        </div>
        <div className="bg-ctp-base border border-ctp-surface0 p-3 rounded-xl">
          <p className="text-[10px] text-ctp-subtext0 uppercase font-bold tracking-wider mb-1">Agent</p>
          <p className="text-white font-mono text-sm truncate">{currentRun.agent_id}</p>
        </div>
        <div className="bg-ctp-base border border-ctp-surface0 p-3 rounded-xl">
          <p className="text-[10px] text-ctp-subtext0 uppercase font-bold tracking-wider mb-1">Team</p>
          <p className="text-white font-medium text-sm truncate">{currentRun.team_id}</p>
        </div>
        <div className="bg-ctp-base border border-ctp-surface0 p-3 rounded-xl">
          <p className="text-[10px] text-ctp-subtext0 uppercase font-bold tracking-wider mb-1">Iterations</p>
          <div className="flex items-end gap-1">
            <span className="text-white font-bold text-lg leading-none">{currentRun.iteration_count}</span>
            <span className="text-ctp-subtext0 text-xs">loops</span>
          </div>
        </div>
        <div className="bg-ctp-base border border-ctp-surface0 p-3 rounded-xl">
          <p className="text-[10px] text-ctp-subtext0 uppercase font-bold tracking-wider mb-1">Tool Calls</p>
          <div className="flex items-end gap-1">
            <span className="text-white font-bold text-lg leading-none">{currentRun.tool_calls?.length ?? 0}</span>
            <span className="material-symbols-outlined text-sm text-ctp-yellow">build</span>
          </div>
        </div>
        <div className="bg-ctp-base border border-ctp-surface0 p-3 rounded-xl">
          <p className="text-[10px] text-ctp-subtext0 uppercase font-bold tracking-wider mb-1">Tokens / Cost</p>
          <p className="text-white font-bold text-sm">{formatTokens(currentRun.total_tokens)} <span className="text-ctp-subtext0 font-normal text-xs">/ {formatCost(currentRun.total_cost_usd)}</span></p>
        </div>
        <div className="bg-ctp-base border border-ctp-surface0 p-3 rounded-xl">
          <p className="text-[10px] text-ctp-subtext0 uppercase font-bold tracking-wider mb-1">Duration</p>
          <p className="text-ctp-blue font-bold text-sm">{currentRun.duration_ms ? formatDuration(currentRun.duration_ms) : '—'}</p>
        </div>
      </div>

      {/* Error banner */}
      {isFailed && currentRun.error && (
        <div className="bg-ctp-red/10 border border-ctp-red/30 rounded-xl px-4 py-3 mb-6 flex items-start gap-3">
          <span className="material-symbols-outlined text-ctp-red shrink-0">error</span>
          <p className="text-sm text-ctp-red">{currentRun.error}</p>
        </div>
      )}

      {/* Error Summary */}
      {totalErrors > 0 && (
        <div className="bg-ctp-surface0/60 border border-ctp-surface1 rounded-xl px-5 py-4 mb-6">
          <div className="flex items-center gap-2 mb-3">
            <span className="material-symbols-outlined text-ctp-yellow text-lg">shield</span>
            <h3 className="text-sm font-bold text-ctp-text">Error Summary</h3>
          </div>
          <div className="flex flex-wrap gap-4">
            {retryCount > 0 && (
              <div className="flex items-center gap-2 bg-ctp-peach/10 border border-ctp-peach/20 rounded-lg px-3 py-2">
                <span className="material-symbols-outlined text-ctp-peach text-sm">autorenew</span>
                <div>
                  <span className="text-xs font-bold text-ctp-peach">
                    {retryCount} retryable {retryCount === 1 ? 'error' : 'errors'}
                  </span>
                  <span className="text-[10px] text-ctp-subtext0 ml-1">
                    ({totalRetries} {totalRetries === 1 ? 'retry' : 'retries'} total, auto-resolved)
                  </span>
                </div>
              </div>
            )}
            {recoverableCount > 0 && (
              <div className="flex items-center gap-2 bg-ctp-yellow/10 border border-ctp-yellow/20 rounded-lg px-3 py-2">
                <span className="material-symbols-outlined text-ctp-yellow text-sm">healing</span>
                <div>
                  <span className="text-xs font-bold text-ctp-yellow">
                    {recoverableCount} recoverable {recoverableCount === 1 ? 'error' : 'errors'}
                  </span>
                  <span className="text-[10px] text-ctp-subtext0 ml-1">(agent self-corrected)</span>
                </div>
              </div>
            )}
            {fatalCount > 0 && (
              <div className="flex items-center gap-2 bg-ctp-red/10 border border-ctp-red/20 rounded-lg px-3 py-2">
                <span className="material-symbols-outlined text-ctp-red text-sm">dangerous</span>
                <div>
                  <span className="text-xs font-bold text-ctp-red">
                    {fatalCount} fatal {fatalCount === 1 ? 'error' : 'errors'}
                  </span>
                  <span className="text-[10px] text-ctp-subtext0 ml-1">(unrecoverable)</span>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Timeline */}
      <div className="max-w-5xl mx-auto flex flex-col gap-0">
        {iterations.map((iter, i) => (
          <div key={iter.index}>
            {i > 0 && <TimelineConnector durationMs={iter.duration_ms} />}
            <IterationCard iteration={iter} defaultExpanded={i === 0} isFailed={isFailed && i === iterations.length - 1} />
          </div>
        ))}

        {/* End of run marker */}
        {iterations.length > 0 && (
          <div className="relative pl-8 flex items-center gap-3 mt-2">
            <div className={`absolute left-[-4px] top-1 size-3 rounded-full border-2 border-ctp-crust shadow-[0_0_10px_rgba(166,227,161,0.6)] ${
              isFailed ? 'bg-ctp-red' : 'bg-ctp-green'
            }`} />
            <span className="text-xs font-bold text-ctp-subtext0 uppercase tracking-widest">
              Run {isFailed ? 'Failed' : 'Finished'}
            </span>
          </div>
        )}
      </div>

      {iterations.length === 0 && (
        <p className="text-sm text-ctp-subtext0 mt-4">No iterations recorded for this run.</p>
      )}
    </div>
  )
}
