interface PlaybackControlsProps {
  currentIndex: number
  maxIndex: number
  playing: boolean
  speed: number
  progress: number
  onIndexChange: (index: number) => void
  onTogglePlay: () => void
  onSpeedChange: (speed: number) => void
}

const SPEEDS = [0.5, 1, 2, 4]

export function PlaybackControls({
  currentIndex,
  maxIndex,
  playing,
  speed,
  progress,
  onIndexChange,
  onTogglePlay,
  onSpeedChange,
}: PlaybackControlsProps) {
  return (
    <div className="bg-ctp-surface0 p-4 rounded-xl border border-ctp-surface1 flex flex-col gap-4">
      <div className="flex items-center justify-between">
        {/* Transport buttons */}
        <div className="flex items-center gap-1">
          <button
            onClick={() => onIndexChange(0)}
            className="w-8 h-8 flex items-center justify-center text-ctp-subtext0 hover:text-ctp-text"
          >
            <span className="material-symbols-outlined text-xl">skip_previous</span>
          </button>
          <button
            onClick={() => onIndexChange(Math.max(0, currentIndex - 1))}
            className="w-8 h-8 flex items-center justify-center text-ctp-subtext0 hover:text-ctp-text"
          >
            <span className="material-symbols-outlined text-xl">fast_rewind</span>
          </button>
          <button
            onClick={onTogglePlay}
            className="w-10 h-10 flex items-center justify-center bg-ctp-blue text-white rounded-full mx-2 shadow-lg shadow-ctp-blue/20"
          >
            <span className="material-symbols-outlined text-2xl" style={{ fontVariationSettings: "'FILL' 1" }}>
              {playing ? 'pause' : 'play_arrow'}
            </span>
          </button>
          <button
            onClick={() => onIndexChange(Math.min(maxIndex, currentIndex + 1))}
            className="w-8 h-8 flex items-center justify-center text-ctp-subtext0 hover:text-ctp-text"
          >
            <span className="material-symbols-outlined text-xl">fast_forward</span>
          </button>
          <button
            onClick={() => onIndexChange(maxIndex)}
            className="w-8 h-8 flex items-center justify-center text-ctp-subtext0 hover:text-ctp-text"
          >
            <span className="material-symbols-outlined text-xl">skip_next</span>
          </button>
        </div>

        {/* Progress bar */}
        <div className="flex-1 px-8">
          <div className="flex items-center justify-between text-[10px] font-mono text-ctp-subtext0 mb-1">
            <span>T-{currentIndex + 1}</span>
            <span className="text-ctp-blue font-bold">{Math.round(progress)}% COMPLETE</span>
            <span>TOTAL: {maxIndex + 1}</span>
          </div>
          <div className="h-2 w-full bg-ctp-mantle rounded-full overflow-hidden cursor-pointer"
            onClick={(e) => {
              const rect = e.currentTarget.getBoundingClientRect()
              const pct = (e.clientX - rect.left) / rect.width
              onIndexChange(Math.round(pct * maxIndex))
            }}
          >
            <div
              className="h-full bg-ctp-blue rounded-full shadow-[0_0_8px_rgba(137,180,250,0.5)] transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>

        {/* Speed selector */}
        <div className="flex items-center gap-1 bg-ctp-mantle p-1 rounded-lg border border-ctp-surface1">
          {SPEEDS.map((s) => (
            <button
              key={s}
              onClick={() => onSpeedChange(s)}
              className={`px-3 py-1 text-xs font-medium rounded transition-colors ${
                speed === s
                  ? 'font-bold text-white bg-ctp-surface2'
                  : 'text-ctp-subtext0 hover:text-ctp-text'
              }`}
            >
              {s}x
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
