import type { ToolCallRecord } from '../../api/types'
import { formatDuration } from '../../utils/format'

export function ToolFrequency({ toolCalls }: { toolCalls: ToolCallRecord[] }) {
  // Count calls, avg duration, errors, and retries per tool
  const stats = new Map<string, { count: number; totalMs: number; errors: number; retries: number }>()
  for (const tc of toolCalls) {
    const existing = stats.get(tc.tool_name) ?? { count: 0, totalMs: 0, errors: 0, retries: 0 }
    existing.count++
    existing.totalMs += tc.duration_ms ?? 0
    existing.retries += tc.retry_count
    const outputData = tc.output_data as Record<string, unknown> | null
    if (tc.error || outputData?.error_type || outputData?.error) {
      existing.errors++
    }
    stats.set(tc.tool_name, existing)
  }

  const data = Array.from(stats.entries())
    .map(([name, s]) => ({
      name,
      count: s.count,
      avgMs: s.count > 0 ? s.totalMs / s.count : 0,
      errors: s.errors,
      retries: s.retries,
    }))
    .sort((a, b) => b.count - a.count)

  const maxCount = Math.max(...data.map((d) => d.count), 1)

  if (data.length === 0) {
    return (
      <div className="bg-ctp-surface0 border border-ctp-surface1 p-6 rounded-xl">
        <h3 className="font-bold text-ctp-text">Tool Frequency</h3>
        <p className="text-xs text-ctp-subtext0 mt-1">No tool calls recorded</p>
      </div>
    )
  }

  return (
    <div className="bg-ctp-surface0 border border-ctp-surface1 p-6 rounded-xl">
      <h3 className="font-bold text-ctp-text">Tool Frequency</h3>
      <p className="text-xs text-ctp-subtext0 mb-8">Calls count and average latency</p>

      <div className="flex flex-col gap-6">
        {data.map((d) => {
          const widthPct = (d.count / maxCount) * 100
          return (
            <div key={d.name} className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between text-xs font-medium">
                <span className="text-ctp-text">{d.name}</span>
                <div className="flex gap-4">
                  <span className="text-ctp-subtext0">
                    {d.count} {d.count === 1 ? 'call' : 'calls'}
                  </span>
                  {d.errors > 0 && (
                    <span className="text-ctp-red">{d.errors} {d.errors === 1 ? 'error' : 'errors'}</span>
                  )}
                  {d.retries > 0 && (
                    <span className="text-ctp-peach">{d.retries} {d.retries === 1 ? 'retry' : 'retries'}</span>
                  )}
                  {d.avgMs > 0 && (
                    <span className="text-ctp-blue">{formatDuration(d.avgMs)} avg</span>
                  )}
                </div>
              </div>
              <div className="h-2.5 w-full bg-ctp-surface1 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${
                    d.errors > 0 ? 'bg-ctp-red' : d.retries > 0 ? 'bg-ctp-peach' : 'bg-ctp-yellow'
                  }`}
                  style={{ width: `${widthPct}%` }}
                />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
