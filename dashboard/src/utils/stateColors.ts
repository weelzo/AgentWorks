import type { AgentState } from '../api/types'

const STATE_COLORS: Record<AgentState, { bg: string; text: string; border: string; dot: string }> = {
  idle: { bg: 'bg-ctp-overlay0/20', text: 'text-ctp-overlay1', border: 'border-ctp-overlay0', dot: 'bg-ctp-overlay1' },
  planning: { bg: 'bg-ctp-blue/20', text: 'text-ctp-blue', border: 'border-ctp-blue', dot: 'bg-ctp-blue' },
  executing_tool: { bg: 'bg-ctp-yellow/20', text: 'text-ctp-yellow', border: 'border-ctp-yellow', dot: 'bg-ctp-yellow' },
  awaiting_llm: { bg: 'bg-ctp-mauve/20', text: 'text-ctp-mauve', border: 'border-ctp-mauve', dot: 'bg-ctp-mauve' },
  reflecting: { bg: 'bg-ctp-teal/20', text: 'text-ctp-teal', border: 'border-ctp-teal', dot: 'bg-ctp-teal' },
  completed: { bg: 'bg-ctp-green/20', text: 'text-ctp-green', border: 'border-ctp-green', dot: 'bg-ctp-green' },
  failed: { bg: 'bg-ctp-red/20', text: 'text-ctp-red', border: 'border-ctp-red', dot: 'bg-ctp-red' },
  suspended: { bg: 'bg-ctp-peach/20', text: 'text-ctp-peach', border: 'border-ctp-peach', dot: 'bg-ctp-peach' },
}

export function getStateColor(state: AgentState) {
  return STATE_COLORS[state] ?? STATE_COLORS.idle
}

/** SVG fill colors (hex) for the state machine visualizer */
export const STATE_HEX: Record<AgentState, string> = {
  idle: '#6c7086',
  planning: '#89b4fa',
  executing_tool: '#f9e2af',
  awaiting_llm: '#cba6f7',
  reflecting: '#94e2d5',
  completed: '#a6e3a1',
  failed: '#f38ba8',
  suspended: '#fab387',
}
