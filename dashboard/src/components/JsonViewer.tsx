import { useState } from 'react'

export function JsonViewer({ data, label, defaultExpanded }: { data: unknown; label?: string; defaultExpanded?: boolean }) {
  const [expanded, setExpanded] = useState(defaultExpanded ?? false)
  const jsonStr = JSON.stringify(data, null, 2)

  return (
    <div>
      <button
        onClick={() => setExpanded(!expanded)}
        className="text-xs text-ctp-subtext0 hover:text-ctp-blue flex items-center gap-1.5 transition-colors"
      >
        <span className="material-symbols-outlined text-xs">{expanded ? 'expand_more' : 'chevron_right'}</span>
        {label ?? 'Raw JSON'}
      </button>
      {expanded && (
        <div className="relative mt-1">
          <pre className="p-3 bg-ctp-crust rounded-lg font-mono text-[11px] text-ctp-subtext1 overflow-x-auto max-h-64 overflow-y-auto leading-relaxed">
            {jsonStr}
          </pre>
          <button
            onClick={() => navigator.clipboard.writeText(jsonStr)}
            className="absolute top-2 right-2 text-ctp-surface2 hover:text-ctp-text transition-colors"
            title="Copy JSON"
          >
            <span className="material-symbols-outlined text-sm">content_copy</span>
          </button>
        </div>
      )}
    </div>
  )
}
