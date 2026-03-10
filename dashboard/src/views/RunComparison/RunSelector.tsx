import { useState } from 'react'
import { useNavigate } from 'react-router'
import type { RunData, AgentState } from '../../api/types'
import { getStateColor } from '../../utils/stateColors'

interface RunSelectorProps {
  leftId: string
  rightId: string
  leftRun?: RunData | null
  rightRun?: RunData | null
}

export function RunSelector({ leftId, rightId, leftRun, rightRun }: RunSelectorProps) {
  const [left, setLeft] = useState(leftId)
  const [right, setRight] = useState(rightId)
  const navigate = useNavigate()

  const handleCompare = () => {
    if (left && right) {
      navigate(`/compare?left=${left}&right=${right}`)
    }
  }

  const handleSwap = () => {
    const tmp = left
    setLeft(right)
    setRight(tmp)
    if (left && right) {
      navigate(`/compare?left=${right}&right=${left}`)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleCompare()
  }

  const leftState = leftRun ? (leftRun.outcome ?? leftRun.state) as AgentState : null
  const rightState = rightRun ? (rightRun.outcome ?? rightRun.state) as AgentState : null
  const leftColors = leftState ? getStateColor(leftState) : null
  const rightColors = rightState ? getStateColor(rightState) : null

  return (
    <div className="grid grid-cols-11 gap-4 items-center">
      {/* Run A */}
      <div className="col-span-5 bg-ctp-surface0 p-4 rounded-xl border border-ctp-surface1">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-bold uppercase tracking-wider text-ctp-subtext0">Run A (Baseline)</span>
          {leftColors && (
            <span className={`px-2 py-0.5 rounded ${leftColors.bg} ${leftColors.text} text-[10px] font-bold`}>
              {(leftState ?? '').toUpperCase()}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <span className="material-symbols-outlined text-ctp-subtext0">search</span>
          <input
            type="text"
            value={left}
            onChange={(e) => setLeft(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search Run A..."
            className="bg-transparent border-none focus:ring-0 text-sm w-full p-0 text-ctp-text placeholder-ctp-subtext0"
          />
        </div>
      </div>

      {/* Swap button */}
      <div className="col-span-1 flex justify-center">
        <button
          onClick={handleSwap}
          className="w-10 h-10 rounded-full bg-ctp-surface1 border border-ctp-surface1 hover:bg-ctp-surface0 flex items-center justify-center text-ctp-text transition-all"
        >
          <span className="material-symbols-outlined">swap_horiz</span>
        </button>
      </div>

      {/* Run B */}
      <div className="col-span-5 bg-ctp-surface0 p-4 rounded-xl border border-ctp-surface1">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-bold uppercase tracking-wider text-ctp-subtext0">Run B (Candidate)</span>
          {rightColors && (
            <span className={`px-2 py-0.5 rounded ${rightColors.bg} ${rightColors.text} text-[10px] font-bold`}>
              {(rightState ?? '').toUpperCase()}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <span className="material-symbols-outlined text-ctp-subtext0">search</span>
          <input
            type="text"
            value={right}
            onChange={(e) => setRight(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search Run B..."
            className="bg-transparent border-none focus:ring-0 text-sm w-full p-0 text-ctp-text placeholder-ctp-subtext0"
          />
        </div>
      </div>
    </div>
  )
}
