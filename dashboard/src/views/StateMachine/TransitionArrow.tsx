interface TransitionArrowProps {
  x1: number
  y1: number
  x2: number
  y2: number
  color: string
  active: boolean
  visited?: boolean
  label?: string
  curve?: 'over' | 'under'
}

export function TransitionArrow({ x1, y1, x2, y2, color, active, visited, label, curve }: TransitionArrowProps) {
  const isVisible = active || visited

  let pathD: string
  let labelX: number
  let labelY: number

  if (curve) {
    // Curved path — arcs over or under the main flow
    const midX = (x1 + x2) / 2
    const curveOffset = curve === 'over' ? -80 : 80
    const cpY = y1 + curveOffset

    // Offset start/end by radius to not overlap circles
    const angle1 = Math.atan2(cpY - y1, midX - x1)
    const angle2 = Math.atan2(y2 - cpY, x2 - midX)
    const r = 28
    const sx = x1 + Math.cos(angle1) * r
    const sy = y1 + Math.sin(angle1) * r
    const ex = x2 - Math.cos(angle2) * r
    const ey = y2 - Math.sin(angle2) * r

    pathD = `M${sx} ${sy} Q${midX} ${cpY} ${ex} ${ey}`
    labelX = midX
    labelY = cpY + (curve === 'over' ? -8 : 14)
  } else {
    // Straight path with circle-edge offset
    const dx = x2 - x1
    const dy = y2 - y1
    const len = Math.sqrt(dx * dx + dy * dy)
    const r = 28

    const ratio1 = r / len
    const ratio2 = (len - r) / len
    const sx = x1 + dx * ratio1
    const sy = y1 + dy * ratio1
    const ex = x1 + dx * ratio2
    const ey = y1 + dy * ratio2

    pathD = `M${sx} ${sy} L${ex} ${ey}`
    labelX = (sx + ex) / 2
    labelY = (sy + ey) / 2 - 8
  }

  return (
    <g>
      <path
        d={pathD}
        fill="none"
        stroke={color}
        strokeWidth={active ? 3 : 2}
        opacity={isVisible ? 1 : 0.15}
        strokeDasharray={isVisible ? 'none' : '4 4'}
        markerEnd="url(#arrowhead)"
        style={{ transition: 'all 0.4s ease' }}
      />
      {label && (
        <text
          x={labelX}
          y={labelY}
          textAnchor="middle"
          fill={color}
          fontSize="8"
          opacity={active ? 0.9 : 0.4}
          style={{ fontFamily: 'Fira Code, monospace', transition: 'opacity 0.3s ease' }}
        >
          {label}
        </text>
      )}
    </g>
  )
}
