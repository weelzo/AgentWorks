import type { RunData } from '../../api/types'
import { formatCost } from '../../utils/format'

export function BudgetBurnRate({ run }: { run: RunData }) {
  const iterCount = run.iteration_count || 1
  if (iterCount === 0 && run.total_cost_usd === 0) {
    return (
      <div className="bg-ctp-surface0 border border-ctp-surface1 p-6 rounded-xl">
        <h3 className="font-bold text-ctp-text">Budget Burn Rate</h3>
        <p className="text-xs text-ctp-subtext0 mt-1">No iteration data available</p>
      </div>
    )
  }

  const totalCost = run.total_cost_usd
  const budget = run.max_budget_usd ?? Math.max(totalCost * 2, 1.0)

  // Build cumulative data points based on actual iteration count
  const points = Array.from({ length: iterCount }, (_, i) => ({
    step: i + 1,
    cost: totalCost * ((i + 1) / iterCount),
  }))
  points.unshift({ step: 0, cost: 0 })

  // Scale Y-axis to whichever is larger: actual final cost or budget
  // But use at least 120% of actual cost so the line doesn't hug the top
  const yMax = Math.max(totalCost * 1.3, budget * 0.1, 0.001)

  // Generate SVG path — scale to viewBox 600x200
  const svgW = 600
  const svgH = 200
  const xScale = svgW / Math.max(points.length - 1, 1)

  const pathPoints = points.map((p, i) => {
    const x = i * xScale
    const y = svgH - Math.min((p.cost / yMax) * svgH, svgH)
    return { x, y }
  })

  const linePath = `M${pathPoints.map(p => `${p.x} ${p.y}`).join(' L')}`
  const areaPath = `${linePath} L${svgW} ${svgH} L0 ${svgH} Z`

  // Budget line position (may be above visible area if budget >> cost)
  const budgetY = svgH - Math.min((budget / yMax) * svgH, svgH)
  const showBudgetLine = budgetY >= 0 && budgetY < svgH

  // Build step labels
  const labels: string[] = ['START']
  if (iterCount > 2) {
    labels.push(`ITER ${Math.ceil(iterCount / 2)}`)
  }
  labels.push('CURRENT')

  return (
    <div className="bg-ctp-surface0 border border-ctp-surface1 p-6 rounded-xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h3 className="font-bold text-ctp-text">Budget Burn Rate</h3>
          <p className="text-xs text-ctp-subtext0">Cumulative cost against ceiling</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs font-mono text-ctp-blue">{formatCost(totalCost)} spent</span>
          <div className="text-xs font-mono text-ctp-subtext0 px-2 py-1 border border-dashed border-ctp-surface1 rounded">
            Limit: {formatCost(budget)}
          </div>
        </div>
      </div>

      <div className="relative h-[240px] w-full">
        {/* Y-axis labels */}
        <div className="absolute left-0 top-0 bottom-8 w-12 flex flex-col justify-between text-[9px] font-mono text-ctp-subtext0 pr-2 text-right">
          <span>{formatCost(yMax)}</span>
          <span>{formatCost(yMax / 2)}</span>
          <span>$0</span>
        </div>

        <div className="ml-14 relative h-[200px]">
          {/* Grid lines */}
          <div className="absolute inset-0 flex flex-col justify-between">
            <div className="h-px w-full bg-ctp-surface1/30" />
            <div className="h-px w-full bg-ctp-surface1/30" />
            <div className="h-px w-full bg-ctp-surface1/30" />
            <div className="h-px w-full bg-ctp-surface1/30" />
            <div className="h-px w-full bg-ctp-surface1/30" />
          </div>

          {/* Budget reference line */}
          {showBudgetLine && (
            <div
              className="absolute w-full border-t-2 border-dashed border-ctp-red/50 z-10"
              style={{ top: `${budgetY}px` }}
            >
              <span className="absolute right-0 -top-4 text-[9px] text-ctp-red font-mono">BUDGET</span>
            </div>
          )}

          {/* SVG area chart */}
          <svg className="absolute inset-0 w-full h-full" viewBox={`0 0 ${svgW} ${svgH}`} preserveAspectRatio="none">
            <defs>
              <linearGradient id="burnGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#89b4fa" stopOpacity={0.4} />
                <stop offset="100%" stopColor="#89b4fa" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <path d={areaPath} fill="url(#burnGradient)" />
            <path d={linePath} fill="none" stroke="#89b4fa" strokeWidth="2.5" />
            {/* Data points */}
            {pathPoints.map((p, i) => (
              <circle key={i} cx={p.x} cy={p.y} r="3" fill="#89b4fa" opacity={i === 0 ? 0 : 0.8} />
            ))}
          </svg>
        </div>

        {/* Bottom labels */}
        <div className="ml-14 flex justify-between text-[10px] font-bold text-ctp-subtext0 pt-2">
          {labels.map((l) => <span key={l}>{l}</span>)}
        </div>
      </div>
    </div>
  )
}
