import { formatDuration } from '../../utils/format'

export function TimelineConnector({ durationMs }: { durationMs?: number }) {
  return (
    <div className="relative h-12 flex items-center justify-center">
      <div className="absolute left-0 top-0 bottom-0 w-1 bg-ctp-surface0" />
      {durationMs != null && durationMs > 0 && (
        <div className="bg-ctp-surface0 px-3 py-1 rounded-full border border-ctp-surface0 text-[10px] font-bold text-ctp-subtext0 flex items-center gap-1.5 z-10">
          <span className="material-symbols-outlined text-xs">hourglass_empty</span>
          {formatDuration(durationMs)} ELAPSED
        </div>
      )}
    </div>
  )
}
