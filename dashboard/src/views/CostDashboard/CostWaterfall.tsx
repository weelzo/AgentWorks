import type { RunData } from '../../api/types'
import { useIterations } from '../../hooks/useIterations'
import { formatTokens, formatCost } from '../../utils/format'

export function CostWaterfall({ run }: { run: RunData }) {
  const iterations = useIterations(run)

  // Build per-iteration data
  let data = iterations.map((iter) => ({
    label: `Iter ${iter.index + 1}`,
    planning: iter.planning?.cost ?? 0,
    planningTokens: iter.planning?.tokens ?? 0,
    reflection: iter.reflection?.cost ?? 0,
    reflectionTokens: iter.reflection?.tokens ?? 0,
    total: iter.totalCost,
    totalTokens: iter.totalTokens,
  }))

  const hasCosts = data.some((d) => d.total > 0)
  const hasTokens = data.some((d) => d.totalTokens > 0)

  // Fallback: if useIterations couldn't match per-step tokens,
  // distribute the run's total tokens evenly across iterations
  if (!hasTokens && !hasCosts && data.length > 0 && run.total_tokens > 0) {
    const tokensPerIter = Math.round(run.total_tokens / data.length)
    // Estimate 60% planning, 40% reflection split
    data = data.map((d) => ({
      ...d,
      planningTokens: Math.round(tokensPerIter * 0.6),
      reflectionTokens: Math.round(tokensPerIter * 0.4),
      totalTokens: tokensPerIter,
    }))
  }

  if (data.length === 0) {
    return (
      <div className="bg-ctp-surface0 border border-ctp-surface1 p-6 rounded-xl">
        <h3 className="font-bold text-ctp-text">Cost Per Iteration</h3>
        <p className="text-xs text-ctp-subtext0 mt-1">No iteration data available</p>
      </div>
    )
  }

  const showCosts = hasCosts
  const maxVal = showCosts
    ? Math.max(...data.map((d) => d.total), 0.001)
    : Math.max(...data.map((d) => d.totalTokens), 1)

  // Chart area height in pixels
  const chartH = 200

  return (
    <div className="bg-ctp-surface0 border border-ctp-surface1 p-6 rounded-xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h3 className="font-bold text-ctp-text">{showCosts ? 'Cost Per Iteration' : 'Tokens Per Iteration'}</h3>
          <p className="text-xs text-ctp-subtext0">{showCosts ? 'Cost distribution per step category' : 'Token distribution per step category'}</p>
        </div>
        <div className="flex gap-4">
          <div className="flex items-center gap-1.5 text-[10px] font-medium text-ctp-subtext0">
            <span className="w-2 h-2 rounded-full bg-ctp-blue" /> Planning
          </div>
          <div className="flex items-center gap-1.5 text-[10px] font-medium text-ctp-subtext0">
            <span className="w-2 h-2 rounded-full bg-ctp-yellow" /> Tool
          </div>
          <div className="flex items-center gap-1.5 text-[10px] font-medium text-ctp-subtext0">
            <span className="w-2 h-2 rounded-full bg-ctp-teal" /> Reflection
          </div>
        </div>
      </div>

      <div style={{ height: `${chartH + 30}px` }} className="flex items-end justify-around gap-4 px-4">
        {data.map((d) => {
          const total = showCosts ? d.total : d.totalTokens
          const planning = showCosts ? d.planning : d.planningTokens
          const reflection = showCosts ? d.reflection : d.reflectionTokens
          const barH = maxVal > 0 ? Math.max((total / maxVal) * chartH, 8) : 8
          const planPct = total > 0 ? (planning / total) * 100 : 50
          const reflPct = total > 0 ? (reflection / total) * 100 : 50
          const toolPct = Math.max(0, 100 - planPct - reflPct)

          return (
            <div key={d.label} className="flex-1 flex flex-col items-center gap-2 max-w-[60px]">
              {/* Value label above bar */}
              <span className="text-[10px] text-ctp-subtext0 font-mono">
                {showCosts ? formatCost(total) : formatTokens(total)}
              </span>
              <div
                className="w-full flex flex-col-reverse rounded-t-sm overflow-hidden transition-all duration-500"
                style={{ height: `${barH}px` }}
              >
                <div className="bg-ctp-blue transition-all duration-300" style={{ height: `${planPct}%` }} />
                <div className="bg-ctp-yellow transition-all duration-300" style={{ height: `${toolPct}%` }} />
                <div className="bg-ctp-teal transition-all duration-300" style={{ height: `${reflPct}%` }} />
              </div>
              <span className="text-xs font-bold text-ctp-subtext0">{d.label}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
