import { useEffect, useState, useCallback, useRef } from 'react'
import { useSearchParams } from 'react-router'
import { useRunStore } from '../../store/runStore'
import { StateNode } from './StateNode'
import { TransitionArrow } from './TransitionArrow'
import { PlaybackControls } from './PlaybackControls'
import { CheckpointPanel } from './CheckpointPanel'
import { STATE_HEX } from '../../utils/stateColors'
import type { AgentState } from '../../api/types'

// Horizontal flow matching Stitch design:
// Main row:  IDLE → PLANNING → AWAITING_LLM → EXECUTING_TOOL → REFLECTING → COMPLETED
// Lower row:                    SUSPENDED                       FAILED
const NODE_POSITIONS: Record<AgentState, { x: number; y: number }> = {
  idle:            { x: 100, y: 180 },
  planning:        { x: 250, y: 180 },
  awaiting_llm:    { x: 400, y: 180 },
  executing_tool:  { x: 560, y: 180 },
  reflecting:      { x: 720, y: 180 },
  completed:       { x: 880, y: 180 },
  suspended:       { x: 400, y: 310 },
  failed:          { x: 560, y: 310 },
}

// Edge definitions with optional curve direction
const EDGES: { from: AgentState; to: AgentState; trigger: string; curve?: 'over' | 'under' }[] = [
  { from: 'idle', to: 'planning', trigger: 'start' },
  { from: 'planning', to: 'awaiting_llm', trigger: 'awaiting_llm' },
  { from: 'awaiting_llm', to: 'executing_tool', trigger: 'llm_responded' },
  { from: 'planning', to: 'executing_tool', trigger: 'needs_tool', curve: 'over' },
  { from: 'executing_tool', to: 'reflecting', trigger: 'tool_done' },
  { from: 'reflecting', to: 'completed', trigger: 'has_answer' },
  { from: 'reflecting', to: 'planning', trigger: 'continue', curve: 'over' },
  { from: 'planning', to: 'completed', trigger: 'has_answer', curve: 'over' },
  { from: 'planning', to: 'suspended', trigger: 'budget_exceeded' },
  { from: 'executing_tool', to: 'reflecting', trigger: 'tool_error' },
  { from: 'executing_tool', to: 'failed', trigger: 'fatal_error' },
  { from: 'awaiting_llm', to: 'failed', trigger: 'llm_error' },
]

export function StateMachine() {
  const [searchParams] = useSearchParams()
  const runId = searchParams.get('run')
  const { currentRun, loading, error, loadRun } = useRunStore()

  const [playIndex, setPlayIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const timerRef = useRef<number | null>(null)

  useEffect(() => {
    if (runId) loadRun(runId)
  }, [runId, loadRun])

  useEffect(() => {
    setPlayIndex(0)
    setPlaying(false)
  }, [currentRun?.run_id])

  useEffect(() => {
    if (!playing || !currentRun) return
    const maxIdx = currentRun.state_history.length - 1
    if (playIndex >= maxIdx) {
      setPlaying(false)
      return
    }
    timerRef.current = window.setTimeout(() => {
      setPlayIndex((i) => Math.min(i + 1, maxIdx))
    }, 1000 / speed)
    return () => { if (timerRef.current) clearTimeout(timerRef.current) }
  }, [playing, playIndex, speed, currentRun])

  const togglePlay = useCallback(() => {
    if (!currentRun) return
    if (playIndex >= currentRun.state_history.length - 1) {
      setPlayIndex(0)
    }
    setPlaying((p) => !p)
  }, [playIndex, currentRun])

  if (!runId) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-ctp-subtext0 gap-3">
        <span className="material-symbols-outlined text-6xl text-ctp-surface1">hub</span>
        <p className="text-sm">Enter a run ID to view the state machine</p>
      </div>
    )
  }
  if (loading) return <div className="p-8"><div className="h-96 bg-ctp-surface0 rounded-xl animate-pulse" /></div>
  if (error) return <div className="p-8 text-ctp-red">{error}</div>
  if (!currentRun) return null

  const history = currentRun.state_history
  const maxIdx = Math.max(0, history.length - 1)
  const currentTransition = history[playIndex] ?? null

  const visitedStates = new Set<string>()
  const visitedEdges = new Set<string>()
  const activeEdges = new Set<string>()
  for (let i = 0; i <= playIndex && i < history.length; i++) {
    visitedStates.add(history[i].from)
    visitedStates.add(history[i].to)
    visitedEdges.add(`${history[i].from}->${history[i].to}`)
    if (i === playIndex) {
      activeEdges.add(`${history[i].from}->${history[i].to}`)
    }
  }
  const activeState = currentTransition?.to ?? currentRun.state
  const progress = maxIdx > 0 ? ((playIndex + 1) / (maxIdx + 1)) * 100 : 0

  return (
    <div className="flex-1 overflow-auto p-6 flex flex-col gap-6">
      {/* Title row */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-ctp-text">State Machine Visualizer</h2>
          <p className="text-ctp-subtext0 text-sm">Execution path for <span className="text-ctp-blue">{currentRun.agent_id}</span></p>
        </div>
      </div>

      {/* SVG Diagram */}
      <div className="flex-1 relative bg-ctp-mantle rounded-xl border border-ctp-surface0 overflow-hidden flex items-center justify-center min-h-[440px]">
        {/* Status indicator */}
        <div className="absolute top-4 right-4 bg-ctp-base/80 backdrop-blur-sm border border-ctp-surface0 p-3 rounded-lg flex flex-col gap-1 z-10">
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${playing ? 'bg-ctp-yellow animate-pulse' : 'bg-ctp-green'}`} />
            <span className="text-xs font-bold text-ctp-text">{playing ? 'CURRENTLY ACTIVE' : 'PAUSED'}</span>
          </div>
          <span className="text-[10px] text-ctp-subtext0 font-mono uppercase tracking-widest">
            Transition {playIndex + 1} of {maxIdx + 1}
          </span>
        </div>

        <svg viewBox="0 0 1000 400" width="1000" height="400" className="max-w-full max-h-full">
          <defs>
            <marker id="arrowhead" markerWidth="10" markerHeight="10" refX="28" refY="3" orient="auto" markerUnits="strokeWidth">
              <path d="M0,0 L0,6 L6,3 z" fill="#585b70" />
            </marker>
            <marker id="arrowhead-active" markerWidth="10" markerHeight="10" refX="28" refY="3" orient="auto" markerUnits="strokeWidth">
              <path d="M0,0 L0,6 L6,3 z" fill="currentColor" />
            </marker>
          </defs>

          {EDGES.map((edge) => {
            const from = NODE_POSITIONS[edge.from]
            const to = NODE_POSITIONS[edge.to]
            const edgeKey = `${edge.from}->${edge.to}`
            const isActive = activeEdges.has(edgeKey)
            const isVisited = visitedEdges.has(edgeKey)
            // Use red/orange for error edges even when not targeting the 'failed' state
            const isErrorEdge = edge.trigger === 'tool_error' || edge.trigger === 'fatal_error' || edge.trigger === 'llm_error'
            const color = isActive
              ? isErrorEdge ? '#fab387' : STATE_HEX[edge.to]  // ctp-peach for error edges
              : '#585b70'
            return (
              <TransitionArrow
                key={`${edgeKey}-${edge.trigger}`}
                x1={from.x} y1={from.y}
                x2={to.x} y2={to.y}
                color={color}
                active={isActive}
                visited={isVisited}
                label={isVisited ? edge.trigger : undefined}
                curve={edge.curve}
              />
            )
          })}

          {(Object.entries(NODE_POSITIONS) as [AgentState, { x: number; y: number }][]).map(
            ([state, pos]) => (
              <StateNode
                key={state}
                state={state}
                x={pos.x}
                y={pos.y}
                active={state === activeState}
                visited={visitedStates.has(state)}
              />
            ),
          )}
        </svg>
      </div>

      {/* Playback Controls */}
      {history.length > 0 && (
        <PlaybackControls
          currentIndex={playIndex}
          maxIndex={maxIdx}
          playing={playing}
          speed={speed}
          progress={progress}
          onIndexChange={setPlayIndex}
          onTogglePlay={togglePlay}
          onSpeedChange={setSpeed}
        />
      )}

      {/* Transition Detail Panel */}
      <CheckpointPanel
        transition={currentTransition}
        run={currentRun}
        transitionIndex={playIndex}
      />
    </div>
  )
}
