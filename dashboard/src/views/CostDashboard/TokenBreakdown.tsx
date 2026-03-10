import type { TokenUsage } from '../../api/types'
import { formatTokens } from '../../utils/format'

interface TokenBreakdownProps {
  usage: TokenUsage
  totalTokens: number
}

export function TokenBreakdown({ usage, totalTokens }: TokenBreakdownProps) {
  const prompt = usage.prompt_tokens
  const completion = usage.completion_tokens
  const total = prompt + completion || totalTokens || 1
  const promptPct = Math.round((prompt / total) * 100)
  const completionPct = 100 - promptPct

  // SVG donut params
  const r = 40
  const circumference = 2 * Math.PI * r
  const promptDash = (promptPct / 100) * circumference
  const completionDash = (completionPct / 100) * circumference

  if (total <= 1) {
    return (
      <div className="bg-ctp-surface0 border border-ctp-surface1 p-6 rounded-xl">
        <h3 className="font-bold text-ctp-text">Token Breakdown</h3>
        <p className="text-xs text-ctp-subtext0 mt-1">No token data available</p>
      </div>
    )
  }

  return (
    <div className="bg-ctp-surface0 border border-ctp-surface1 p-6 rounded-xl">
      <h3 className="font-bold text-ctp-text">Token Breakdown</h3>
      <p className="text-xs text-ctp-subtext0 mb-8">Prompt vs Completion distribution</p>

      <div className="flex items-center justify-center gap-12 h-[240px]">
        {/* Donut chart */}
        <div className="relative w-40 h-40">
          <svg className="w-full h-full transform -rotate-90" viewBox="0 0 100 100">
            {/* Prompt slice (blue) */}
            <circle
              cx="50" cy="50" r={r}
              fill="transparent"
              stroke="#89b4fa"
              strokeWidth="12"
              strokeDasharray={circumference}
              strokeDashoffset={circumference - promptDash}
              style={{ transition: 'stroke-dashoffset 0.6s ease' }}
            />
            {/* Completion slice (lavender) */}
            <circle
              cx="50" cy="50" r={r}
              fill="transparent"
              stroke="#b4befe"
              strokeWidth="12"
              strokeDasharray={circumference}
              strokeDashoffset={circumference - completionDash}
              transform={`rotate(${promptPct * 3.6} 50 50)`}
              style={{ transition: 'stroke-dashoffset 0.6s ease' }}
            />
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
            <span className="text-lg font-bold text-ctp-text leading-none">{formatTokens(total)}</span>
            <span className="text-[10px] text-ctp-subtext0 uppercase font-medium">Tokens</span>
          </div>
        </div>

        {/* Legend */}
        <div className="flex flex-col gap-4">
          <div className="flex flex-col">
            <div className="flex items-center gap-2 text-sm font-bold text-ctp-text">
              <span className="w-3 h-3 rounded-full bg-ctp-blue" /> {promptPct}%
            </div>
            <span className="text-[11px] text-ctp-subtext0 ml-5">Prompt ({formatTokens(prompt)})</span>
          </div>
          <div className="flex flex-col">
            <div className="flex items-center gap-2 text-sm font-bold text-ctp-text">
              <span className="w-3 h-3 rounded-full bg-ctp-lavender" /> {completionPct}%
            </div>
            <span className="text-[11px] text-ctp-subtext0 ml-5">Completion ({formatTokens(completion)})</span>
          </div>
        </div>
      </div>
    </div>
  )
}
