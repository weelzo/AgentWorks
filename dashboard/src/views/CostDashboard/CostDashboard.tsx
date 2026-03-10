import { useEffect } from 'react'
import { useSearchParams } from 'react-router'
import { useRunStore } from '../../store/runStore'
import { formatCost, formatTokens } from '../../utils/format'
import { CostWaterfall } from './CostWaterfall'
import { TokenBreakdown } from './TokenBreakdown'
import { BudgetBurnRate } from './BudgetBurnRate'
import { ToolFrequency } from './ToolFrequency'
import { ExecutionLog } from './ExecutionLog'
import type { AgentState } from '../../api/types'
import { getStateColor } from '../../utils/stateColors'

export function CostDashboard() {
  const [searchParams] = useSearchParams()
  const runId = searchParams.get('run')
  const { currentRun, loading, error, loadRun } = useRunStore()

  useEffect(() => {
    if (runId) loadRun(runId)
  }, [runId, loadRun])

  if (!runId) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-ctp-subtext0 gap-3">
        <span className="material-symbols-outlined text-6xl text-ctp-surface1">payments</span>
        <p className="text-sm">Enter a run ID to view cost data.</p>
      </div>
    )
  }
  if (loading) {
    return (
      <div className="p-8 space-y-6">
        <div className="grid grid-cols-4 gap-6">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-28 bg-ctp-surface0 rounded-xl animate-pulse" />
          ))}
        </div>
        <div className="grid grid-cols-2 gap-6">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-72 bg-ctp-surface0 rounded-xl animate-pulse" />
          ))}
        </div>
      </div>
    )
  }
  if (error) return <div className="p-8 text-ctp-red">{error}</div>
  if (!currentRun) return null

  const budget = currentRun.max_budget_usd ?? currentRun.total_cost_usd * 2
  const budgetPct = budget > 0 ? (currentRun.total_cost_usd / budget) * 100 : 0
  const avgTokensPerIter = currentRun.iteration_count > 0
    ? Math.round(currentRun.total_tokens / currentRun.iteration_count)
    : 0
  const stateColors = getStateColor(currentRun.state as AgentState)

  // SVG circular progress for budget card
  const circumference = 2 * Math.PI * 20 // r=20
  const dashOffset = circumference - (circumference * Math.min(budgetPct, 100)) / 100
  const budgetColor = budgetPct > 80 ? 'text-ctp-red' : budgetPct > 50 ? 'text-ctp-yellow' : 'text-ctp-green'

  return (
    <div className="flex-1 overflow-auto p-6 space-y-6">
      {/* Title */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-ctp-text">Cost & Token Dashboard</h2>
          <p className="text-ctp-subtext0 text-sm">
            Resource usage for <span className="text-ctp-blue">{currentRun.agent_id}</span>
          </p>
        </div>
        <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full border ${stateColors.border} ${stateColors.bg}`}>
          <div className={`w-2 h-2 rounded-full ${stateColors.dot}`} />
          <span className={`text-xs font-bold uppercase ${stateColors.text}`}>
            {currentRun.state}
          </span>
        </div>
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        {/* Total Cost */}
        <div className="bg-ctp-surface0 border border-ctp-surface1 p-5 rounded-xl flex flex-col gap-1 relative overflow-hidden group">
          <div className="absolute right-0 top-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity">
            <span className="material-symbols-outlined text-4xl">payments</span>
          </div>
          <span className="text-sm font-medium text-ctp-subtext0">Total Cost</span>
          <div className="text-2xl font-bold text-ctp-text">{formatCost(currentRun.total_cost_usd)}</div>
          <div className="text-xs text-ctp-green flex items-center gap-1 mt-1">
            <span className="material-symbols-outlined text-xs">trending_up</span>
            {formatCost(currentRun.total_cost_usd / Math.max(currentRun.iteration_count, 1))} per iter
          </div>
        </div>

        {/* Total Tokens */}
        <div className="bg-ctp-surface0 border border-ctp-surface1 p-5 rounded-xl flex flex-col gap-1 relative overflow-hidden group">
          <div className="absolute right-0 top-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity">
            <span className="material-symbols-outlined text-4xl">toll</span>
          </div>
          <span className="text-sm font-medium text-ctp-subtext0">Total Tokens</span>
          <div className="text-2xl font-bold text-ctp-text">{formatTokens(currentRun.total_tokens)}</div>
          <div className="text-xs text-ctp-blue flex items-center gap-1 mt-1">
            Avg. {formatTokens(avgTokensPerIter)} per iter
          </div>
        </div>

        {/* Iterations */}
        <div className="bg-ctp-surface0 border border-ctp-surface1 p-5 rounded-xl flex flex-col gap-1 relative overflow-hidden group">
          <div className="absolute right-0 top-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity">
            <span className="material-symbols-outlined text-4xl">reorder</span>
          </div>
          <span className="text-sm font-medium text-ctp-subtext0">Iterations</span>
          <div className="text-2xl font-bold text-ctp-text">
            {currentRun.iteration_count} / {currentRun.max_iterations ?? 25}
          </div>
          <div className="text-xs text-ctp-subtext0 flex items-center gap-1 mt-1">
            Max limit: {currentRun.max_iterations ?? 25} iters
          </div>
        </div>

        {/* Budget Used with circular progress */}
        <div className="bg-ctp-surface0 border border-ctp-surface1 p-5 rounded-xl flex flex-col gap-1 relative overflow-hidden group">
          <div className="absolute right-4 top-4">
            <div className="relative w-12 h-12">
              <svg className="w-full h-full transform -rotate-90">
                <circle
                  className="text-ctp-surface1"
                  cx="24" cy="24" r="20"
                  fill="transparent" stroke="currentColor" strokeWidth="4"
                />
                <circle
                  className={budgetColor}
                  cx="24" cy="24" r="20"
                  fill="transparent" stroke="currentColor" strokeWidth="4"
                  strokeDasharray={circumference}
                  strokeDashoffset={dashOffset}
                  strokeLinecap="round"
                  style={{ transition: 'stroke-dashoffset 0.6s ease' }}
                />
              </svg>
              <span className={`absolute inset-0 flex items-center justify-center text-[10px] font-bold ${budgetColor}`}>
                {budgetPct.toFixed(budgetPct < 10 ? 1 : 0)}%
              </span>
            </div>
          </div>
          <span className="text-sm font-medium text-ctp-subtext0">Budget Used</span>
          <div className="text-2xl font-bold text-ctp-text">{budgetPct.toFixed(1)}%</div>
          <div className="text-xs text-ctp-subtext0 mt-1">
            {formatCost(currentRun.total_cost_usd)} / {formatCost(budget)}
          </div>
        </div>
      </div>

      {/* Chart Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <CostWaterfall run={currentRun} />
        <TokenBreakdown usage={currentRun.token_usage} totalTokens={currentRun.total_tokens} />
        <BudgetBurnRate run={currentRun} />
        <ToolFrequency toolCalls={currentRun.tool_calls} />
      </div>

      {/* Execution Log */}
      <ExecutionLog run={currentRun} />
    </div>
  )
}
