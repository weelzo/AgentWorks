import type { StateTransition, RunData } from '../../api/types'
import { formatTimestamp, formatCost } from '../../utils/format'
import { getStateColor } from '../../utils/stateColors'
import type { AgentState } from '../../api/types'

interface CheckpointPanelProps {
  transition: StateTransition | null
  run: RunData
  transitionIndex: number
}

export function CheckpointPanel({ transition, run, transitionIndex }: CheckpointPanelProps) {
  if (!transition) {
    return (
      <div className="bg-ctp-surface0 rounded-xl border border-ctp-surface1 p-6 text-sm text-ctp-subtext0 flex items-center gap-3">
        <span className="material-symbols-outlined">info</span>
        Scrub the timeline or click play to inspect transitions.
      </div>
    )
  }

  const totalTransitions = run.state_history.length
  const progress = totalTransitions > 0 ? transitionIndex / totalTransitions : 0
  const estimatedCostAtPoint = run.total_cost_usd * progress
  const fromColors = getStateColor(transition.from as AgentState)
  const toColors = getStateColor(transition.to as AgentState)

  return (
    <div className="bg-ctp-surface0 rounded-xl border border-ctp-surface1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-ctp-surface1 bg-ctp-mantle/50 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-xs font-bold bg-ctp-surface2 px-2 py-1 rounded">
            TRANSITION {transitionIndex + 1} / {totalTransitions}
          </span>
          <div className="flex items-center gap-2 text-sm">
            <span className={`font-medium ${fromColors.text}`}>{transition.from.toUpperCase()}</span>
            <span className="material-symbols-outlined text-ctp-subtext0 text-base">trending_flat</span>
            <span className={`font-medium ${toColors.text}`}>{transition.to.toUpperCase()}</span>
          </div>
        </div>
        <div className="text-xs text-ctp-subtext0">
          Trigger: <span className="text-ctp-lavender font-mono">{transition.trigger}</span>
        </div>
      </div>

      {/* Stats grid */}
      <div className="p-4 grid grid-cols-1 md:grid-cols-4 gap-6">
        <div className="space-y-1">
          <p className="text-[10px] uppercase tracking-wider text-ctp-subtext0">Iteration</p>
          <p className="text-xl font-bold text-ctp-text">{Math.ceil((transitionIndex + 1) / 3)}</p>
        </div>
        <div className="space-y-1">
          <p className="text-[10px] uppercase tracking-wider text-ctp-subtext0">Budget Remaining</p>
          <p className="text-xl font-bold text-ctp-green">{formatCost(Math.max(0, (run.max_budget_usd ?? 1) - estimatedCostAtPoint))}</p>
        </div>
        <div className="space-y-1">
          <p className="text-[10px] uppercase tracking-wider text-ctp-subtext0">Token Usage</p>
          <p className="text-xl font-bold text-ctp-text">
            {Math.round(run.total_tokens * progress)} <span className="text-sm font-normal text-ctp-subtext0">tokens</span>
          </p>
        </div>
        <div className="space-y-1">
          <p className="text-[10px] uppercase tracking-wider text-ctp-subtext0">Timestamp</p>
          <p className="text-xl font-bold text-ctp-blue">{formatTimestamp(transition.timestamp)}</p>
        </div>
      </div>
    </div>
  )
}
