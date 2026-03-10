import type { RunData, AgentState, StateTransition } from '../../api/types'
import { findDivergence } from '../../utils/diffEngine'

export function TimelineDiff({ left, right }: { left: RunData; right: RunData }) {
  const divergence = findDivergence(left.state_history, right.state_history)
  const divIdx = divergence.divergenceIndex

  // Calculate pixel offset for divergence line (each card ~80px + gap 16px)
  const cardHeight = 80
  const gap = 16
  const divergenceTop = divIdx !== null ? (divIdx * (cardHeight + gap)) + (cardHeight / 2) : null

  return (
    <section className="space-y-4">
      <h3 className="text-sm font-bold uppercase tracking-widest text-ctp-subtext0 px-1">
        Iteration Timeline Comparison
      </h3>

      <div className="relative grid grid-cols-2 gap-8 min-h-[200px]">
        {/* Divergence line */}
        {divergenceTop !== null && (
          <div
            className="absolute inset-x-0 z-10 flex items-center gap-4 pointer-events-none"
            style={{ top: `${divergenceTop}px` }}
          >
            <div className="flex-1 border-t-2 border-dashed border-ctp-yellow/60" />
            <span className="px-3 py-1 rounded bg-ctp-yellow text-ctp-crust text-[10px] font-black uppercase whitespace-nowrap">
              Runs diverged here
            </span>
            <div className="flex-1 border-t-2 border-dashed border-ctp-yellow/60" />
          </div>
        )}

        {/* Run A Timeline */}
        <div className="space-y-4">
          {left.state_history.map((t, i) => (
            <TimelineCard
              key={i}
              transition={t}
              index={i}
              isDiverged={divIdx !== null && i >= divIdx}
              isSuccess={i === left.state_history.length - 1 && (left.outcome ?? left.state) === 'completed'}
              isFailed={i === left.state_history.length - 1 && (left.outcome ?? left.state) === 'failed'}
            />
          ))}
          {/* Final state card */}
          <FinalStateCard state={(left.outcome ?? left.state) as AgentState} />
        </div>

        {/* Run B Timeline */}
        <div className="space-y-4">
          {right.state_history.map((t, i) => (
            <TimelineCard
              key={i}
              transition={t}
              index={i}
              isDiverged={divIdx !== null && i >= divIdx}
              isSuccess={false}
              isFailed={t.trigger === 'fatal_error' || t.trigger === 'llm_error' || t.trigger === 'tool_error'}
            />
          ))}
          <FinalStateCard state={(right.outcome ?? right.state) as AgentState} />
        </div>
      </div>
    </section>
  )
}

function TimelineCard({ transition, index, isDiverged, isSuccess, isFailed }: {
  transition: StateTransition
  index: number
  isDiverged: boolean
  isSuccess: boolean
  isFailed: boolean
}) {
  let cardClass = 'bg-ctp-surface0 border border-ctp-surface1'
  let circleClass = 'bg-ctp-blue/20 text-ctp-blue'

  if (!isDiverged) {
    // Pre-divergence: dimmed
    cardClass = 'opacity-30 bg-ctp-surface0 border border-ctp-surface1'
  } else if (isFailed) {
    cardClass = 'bg-ctp-red/10 border-l-4 border-ctp-red border-y border-r border-ctp-red/20'
    circleClass = 'bg-ctp-red/20 text-ctp-red'
  } else if (isSuccess) {
    cardClass = 'bg-ctp-green/10 border border-ctp-green/30'
    circleClass = 'bg-ctp-green text-ctp-crust'
  } else if (isDiverged) {
    // Post-divergence, non-error
    cardClass = 'bg-ctp-surface0 border-l-4 border-ctp-yellow border-y border-r border-ctp-surface1'
    circleClass = 'bg-ctp-yellow/20 text-ctp-yellow'
  }

  return (
    <div className={`flex gap-4 p-4 rounded-lg ${cardClass}`}>
      <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 text-xs font-bold ${circleClass}`}>
        {isSuccess ? (
          <span className="material-symbols-outlined text-sm">check</span>
        ) : isFailed ? (
          <span className="material-symbols-outlined text-sm">close</span>
        ) : (
          index + 1
        )}
      </div>
      <div className="space-y-1 min-w-0">
        <p className={`text-sm font-semibold ${isFailed ? 'text-ctp-red' : ''}`}>
          {transition.from} → {transition.to}
        </p>
        <p className="text-xs text-ctp-subtext0">
          Trigger: <span className="font-mono">{transition.trigger}</span>
        </p>
      </div>
    </div>
  )
}

function FinalStateCard({ state }: { state: AgentState }) {
  const isCompleted = state === 'completed'
  const isFailed = state === 'failed'

  if (isCompleted) {
    return (
      <div className="flex gap-4 p-4 rounded-lg bg-ctp-green/10 border border-ctp-green/30">
        <div className="w-8 h-8 rounded-full bg-ctp-green text-ctp-crust flex items-center justify-center shrink-0">
          <span className="material-symbols-outlined text-sm font-bold">check</span>
        </div>
        <div className="space-y-1">
          <p className="text-sm font-bold text-ctp-green">Run Completed</p>
          <p className="text-xs text-ctp-subtext0">Successfully reached final state.</p>
        </div>
      </div>
    )
  }

  if (isFailed) {
    return (
      <div className="flex gap-4 p-4 rounded-lg bg-ctp-red border border-ctp-red shadow-lg shadow-ctp-red/20">
        <div className="w-8 h-8 rounded-full bg-ctp-crust text-ctp-red flex items-center justify-center shrink-0">
          <span className="material-symbols-outlined text-sm font-bold">close</span>
        </div>
        <div className="space-y-1">
          <p className="text-sm font-black text-ctp-crust uppercase">Run Failed</p>
          <p className="text-xs text-ctp-crust font-medium">Execution terminated with errors.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex gap-4 p-4 rounded-lg bg-ctp-surface0 border border-ctp-surface1">
      <div className="w-8 h-8 rounded-full bg-ctp-surface1 text-ctp-subtext0 flex items-center justify-center shrink-0">
        <span className="material-symbols-outlined text-sm">pending</span>
      </div>
      <div className="space-y-1">
        <p className="text-sm font-semibold text-ctp-subtext0">{state.toUpperCase()}</p>
        <p className="text-xs text-ctp-subtext0">Run ended in this state.</p>
      </div>
    </div>
  )
}
