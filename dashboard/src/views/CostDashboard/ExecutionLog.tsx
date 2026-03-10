import type { RunData } from '../../api/types'
import { formatTimestamp, formatDuration } from '../../utils/format'

export function ExecutionLog({ run }: { run: RunData }) {
  // Build log entries from state_history and tool_calls
  const entries: { timestamp: string; action: string; icon: string; iconColor: string; latency: string; cost: string }[] = []

  for (const tc of run.tool_calls) {
    const outputData = tc.output_data as Record<string, unknown> | null
    const isRecoverableError = !tc.error && (outputData?.error_type || outputData?.error)
    const hasError = tc.error || isRecoverableError

    let actionLabel = `Tool: ${tc.tool_name}`
    if (tc.retry_count > 0) actionLabel += ` (retried ${tc.retry_count}x)`
    if (tc.error) actionLabel += ' — FATAL'
    else if (isRecoverableError) actionLabel += ' — RECOVERABLE'

    entries.push({
      timestamp: tc.started_at ?? '',
      action: actionLabel,
      icon: hasError ? 'error' : tc.retry_count > 0 ? 'replay' : 'construction',
      iconColor: hasError ? 'text-ctp-red' : tc.retry_count > 0 ? 'text-ctp-peach' : 'text-ctp-yellow',
      latency: tc.duration_ms ? formatDuration(tc.duration_ms) : '—',
      cost: '—',
    })
  }

  for (const t of run.state_history) {
    // Skip noisy internal transitions (awaiting_llm↔planning cycle)
    if (t.from === 'awaiting_llm' && t.to === 'planning') continue
    if (t.from === 'planning' && t.to === 'awaiting_llm') continue

    const icon = t.to === 'completed' ? 'check_circle'
      : t.to === 'failed' ? 'error'
      : t.to === 'executing_tool' ? 'build'
      : 'psychology'
    const color = t.to === 'failed' ? 'text-ctp-red'
      : t.to === 'completed' ? 'text-ctp-green'
      : 'text-ctp-blue'
    entries.push({
      timestamp: t.timestamp,
      action: `${t.from} → ${t.to}`,
      icon,
      iconColor: color,
      latency: t.duration_ms ? formatDuration(t.duration_ms) : '—',
      cost: '—',
    })
  }

  // Sort by timestamp descending, tool calls before state transitions at same time
  entries.sort((a, b) => {
    if (b.timestamp !== a.timestamp) return b.timestamp > a.timestamp ? 1 : -1
    // At same timestamp, tools first
    const aIsTool = a.action.startsWith('Tool:') ? 0 : 1
    const bIsTool = b.action.startsWith('Tool:') ? 0 : 1
    return aIsTool - bIsTool
  })

  // Show up to 15 most recent events
  const visible = entries.slice(0, 15)

  if (visible.length === 0) return null

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-bold text-ctp-text">Recent Execution Context</h3>
        <span className="text-xs text-ctp-blue font-bold">{entries.length} events total</span>
      </div>

      <div className="bg-ctp-surface0 border border-ctp-surface1 rounded-xl overflow-hidden">
        <table className="w-full text-left text-sm">
          <thead className="bg-ctp-mantle text-ctp-subtext0 font-medium">
            <tr>
              <th className="px-6 py-3 border-b border-ctp-surface1">Timestamp</th>
              <th className="px-6 py-3 border-b border-ctp-surface1">Action</th>
              <th className="px-6 py-3 border-b border-ctp-surface1">Latency</th>
              <th className="px-6 py-3 border-b border-ctp-surface1 text-right">Cost</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ctp-surface1">
            {visible.map((entry, i) => (
              <tr key={i} className="hover:bg-ctp-surface1/30 transition-colors">
                <td className="px-6 py-4 font-mono text-[12px] text-ctp-subtext0">
                  {entry.timestamp ? formatTimestamp(entry.timestamp) : '—'}
                </td>
                <td className="px-6 py-4">
                  <div className="flex items-center gap-2">
                    <span className={`material-symbols-outlined text-sm ${entry.iconColor}`}>{entry.icon}</span>
                    <span className="text-ctp-text">{entry.action}</span>
                  </div>
                </td>
                <td className="px-6 py-4 text-ctp-blue">{entry.latency}</td>
                <td className="px-6 py-4 text-right text-ctp-text">{entry.cost}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
