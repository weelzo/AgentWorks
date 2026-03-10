import { STATE_HEX } from '../../utils/stateColors'
import type { AgentState } from '../../api/types'

interface StateNodeProps {
  state: AgentState
  x: number
  y: number
  active: boolean
  visited: boolean
}

export function StateNode({ state, x, y, active, visited }: StateNodeProps) {
  const color = STATE_HEX[state]
  const opacity = visited ? 1 : 0.3
  const label = state.toUpperCase()

  return (
    <g transform={`translate(${x}, ${y})`} style={{ transition: 'all 0.3s ease' }}>
      {/* Glow for active state */}
      {active && (
        <circle r="32" fill="none" stroke={color} strokeWidth="2" opacity="0.4">
          <animate attributeName="r" values="32;38;32" dur="2s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="0.4;0.15;0.4" dur="2s" repeatCount="indefinite" />
        </circle>
      )}

      {/* Main circle */}
      <circle
        r="24"
        fill={active ? color : '#1e1e2e'}
        stroke={color}
        strokeWidth={active ? 2.5 : 2}
        opacity={opacity}
        style={{ transition: 'all 0.4s ease' }}
      />

      {/* Inner dot for visited but not active */}
      {visited && !active && (
        <circle r="6" fill={color} opacity={opacity} />
      )}

      {/* Label below */}
      <text
        textAnchor="middle"
        dy="42"
        fill={color}
        fontSize="9"
        fontWeight="bold"
        opacity={opacity}
        style={{ fontFamily: 'Space Grotesk, sans-serif' }}
      >
        {label}
      </text>
    </g>
  )
}
