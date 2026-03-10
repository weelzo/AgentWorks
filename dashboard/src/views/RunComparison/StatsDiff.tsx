import type { RunData } from '../../api/types'
import { formatCost, formatTokens, formatDuration } from '../../utils/format'

export function StatsDiff({ left, right }: { left: RunData; right: RunData }) {
  const leftOutcome = left.outcome ?? left.state
  const rightOutcome = right.outcome ?? right.state

  const rows: { label: string; a: string; b: string; delta: string; highlight?: boolean }[] = [
    {
      label: 'Outcome',
      a: leftOutcome.toUpperCase(),
      b: rightOutcome.toUpperCase(),
      delta: leftOutcome === rightOutcome ? 'SAME' : 'REGRESSED',
      highlight: leftOutcome !== rightOutcome,
    },
    {
      label: 'Iterations',
      a: String(left.iteration_count),
      b: String(right.iteration_count),
      delta: formatNumDelta(right.iteration_count - left.iteration_count),
    },
    {
      label: 'Tool Calls',
      a: String(left.tool_calls.length),
      b: String(right.tool_calls.length),
      delta: formatNumDelta(right.tool_calls.length - left.tool_calls.length),
    },
    {
      label: 'Cost',
      a: formatCost(left.total_cost_usd),
      b: formatCost(right.total_cost_usd),
      delta: left.total_cost_usd > 0
        ? formatPctDelta(left.total_cost_usd, right.total_cost_usd)
        : '—',
    },
    {
      label: 'Tokens',
      a: formatTokens(left.total_tokens),
      b: formatTokens(right.total_tokens),
      delta: left.total_tokens > 0
        ? formatPctDelta(left.total_tokens, right.total_tokens)
        : formatNumDelta(right.total_tokens - left.total_tokens),
    },
    {
      label: 'Duration',
      a: left.duration_ms ? formatDuration(left.duration_ms) : '—',
      b: right.duration_ms ? formatDuration(right.duration_ms) : '—',
      delta: left.duration_ms && right.duration_ms
        ? formatPctDelta(left.duration_ms, right.duration_ms)
        : '—',
    },
  ]

  return (
    <section className="bg-ctp-surface0 rounded-xl border border-ctp-surface1 overflow-hidden">
      <table className="w-full text-left text-sm border-collapse">
        <thead>
          <tr className="bg-ctp-mantle border-b border-ctp-surface1">
            <th className="px-6 py-4 font-semibold text-ctp-subtext0">Metric</th>
            <th className="px-6 py-4 font-semibold text-ctp-subtext0">Run A</th>
            <th className="px-6 py-4 font-semibold text-ctp-subtext0">Run B</th>
            <th className="px-6 py-4 font-semibold text-ctp-subtext0">Delta</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-ctp-surface1">
          {rows.map((row) => (
            <tr key={row.label} className="hover:bg-ctp-base/50 transition-colors">
              <td className={`px-6 py-4 font-medium ${row.highlight ? 'border-l-4 border-ctp-red' : ''}`}>
                {row.label}
              </td>
              <td className="px-6 py-4 text-ctp-subtext0 font-mono">
                <span className={getOutcomeColor(row.label === 'Outcome' ? row.a : '')}>
                  {row.a}
                </span>
              </td>
              <td className="px-6 py-4 text-ctp-subtext0 font-mono">
                <span className={getOutcomeColor(row.label === 'Outcome' ? row.b : '')}>
                  {row.b}
                </span>
              </td>
              <td className="px-6 py-4">
                {row.label === 'Outcome' && row.delta === 'REGRESSED' ? (
                  <span className="px-2 py-0.5 rounded bg-ctp-red/10 text-ctp-red text-xs font-semibold">
                    REGRESSED
                  </span>
                ) : (
                  <span className={`font-semibold ${getDeltaColor(row.delta)}`}>{row.delta}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function formatNumDelta(n: number): string {
  if (n === 0) return '0'
  return n > 0 ? `+${n.toLocaleString()}` : n.toLocaleString()
}

function formatPctDelta(a: number, b: number): string {
  if (a === 0) return '—'
  const pct = ((b - a) / a) * 100
  const sign = pct >= 0 ? '+' : ''
  return `${sign}${pct.toFixed(0)}%`
}

function getDeltaColor(delta: string): string {
  if (delta === '0' || delta === '—' || delta === 'SAME') return 'text-ctp-subtext0'
  if (delta.startsWith('+')) return 'text-ctp-red'
  if (delta.startsWith('-')) return 'text-ctp-green'
  return 'text-ctp-subtext0'
}

function getOutcomeColor(outcome: string): string {
  if (outcome === 'COMPLETED') return 'text-ctp-green font-mono'
  if (outcome === 'FAILED') return 'text-ctp-red font-mono'
  return ''
}
