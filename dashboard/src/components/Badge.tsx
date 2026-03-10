import type { AgentState } from '../api/types'
import { getStateColor } from '../utils/stateColors'

const ICONS: Partial<Record<AgentState, string>> = {
  completed: 'verified',
  failed: 'error',
  suspended: 'pause_circle',
  planning: 'psychology',
  executing_tool: 'build',
  reflecting: 'chat_bubble',
  awaiting_llm: 'hourglass_empty',
}

export function Badge({ state }: { state: AgentState }) {
  const colors = getStateColor(state)
  const icon = ICONS[state]
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-wider ${colors.bg} ${colors.text}`}>
      {icon && <span className="material-symbols-outlined text-sm">{icon}</span>}
      {state.replace('_', ' ')}
    </span>
  )
}
