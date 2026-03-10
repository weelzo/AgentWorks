import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useRunStore } from '../../store/runStore'
import { MessageBubble } from './MessageBubble'
import { formatTokens } from '../../utils/format'
import type { ToolCallRecord } from '../../api/types'

export function ConversationThread() {
  const [searchParams] = useSearchParams()
  const runId = searchParams.get('run')
  const { currentRun, loading, error, loadRun } = useRunStore()
  const endRef = useRef<HTMLDivElement>(null)
  const [showRaw, setShowRaw] = useState(false)

  useEffect(() => {
    if (runId) loadRun(runId)
  }, [runId, loadRun])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [currentRun?.messages.length])

  if (!runId) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-ctp-subtext0 gap-3">
        <span className="material-symbols-outlined text-6xl text-ctp-surface1">chat_bubble</span>
        <p className="text-sm">Enter a run ID to view the conversation.</p>
      </div>
    )
  }
  if (loading) {
    return (
      <div className="p-8 space-y-4 max-w-3xl mx-auto">
        {[...Array(4)].map((_, i) => (
          <div key={i} className={`h-20 bg-ctp-surface0 rounded-2xl animate-pulse ${i % 2 === 0 ? 'w-4/5 ml-auto' : 'w-4/5'}`} />
        ))}
      </div>
    )
  }
  if (error) return <div className="p-8 text-ctp-red">{error}</div>
  if (!currentRun) return null

  // Build tool result map for inline display
  const toolResultMap = new Map<string, ToolCallRecord>()
  for (const tc of currentRun.tool_calls) {
    toolResultMap.set(tc.tool_call_id, tc)
  }

  const messageCount = currentRun.messages.length
  const toolCallCount = currentRun.tool_calls.length
  const errorCount = currentRun.tool_calls.filter(tc => {
    if (tc.error) return true
    const out = tc.output_data as Record<string, unknown> | null
    return out?.error_type || out?.error
  }).length
  const retriedCount = currentRun.tool_calls.filter(tc => tc.retry_count > 0).length

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Stats Bar */}
      <div className="bg-ctp-mantle border-b border-ctp-surface1 px-8 py-3 flex items-center justify-between">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-ctp-subtext0 text-sm">message</span>
            <span className="text-sm font-medium">{messageCount} messages</span>
          </div>
          <div className="flex items-center gap-2 border-l border-ctp-surface1 pl-6">
            <span className="material-symbols-outlined text-ctp-yellow text-sm">build</span>
            <span className="text-sm font-medium">{toolCallCount} tool calls</span>
          </div>
          <div className="flex items-center gap-2 border-l border-ctp-surface1 pl-6">
            <span className="material-symbols-outlined text-ctp-green text-sm">data_usage</span>
            <span className="text-sm font-medium">{formatTokens(currentRun.total_tokens)} tokens</span>
          </div>
          {(errorCount > 0 || retriedCount > 0) && (
            <div className="flex items-center gap-2 border-l border-ctp-surface1 pl-6">
              <span className="material-symbols-outlined text-ctp-yellow text-sm">warning</span>
              <span className="text-sm font-medium">
                {errorCount > 0 && `${errorCount} ${errorCount === 1 ? 'error' : 'errors'}`}
                {errorCount > 0 && retriedCount > 0 && ', '}
                {retriedCount > 0 && `${retriedCount} retried`}
              </span>
            </div>
          )}
        </div>
        <div className="flex items-center bg-ctp-crust rounded-lg p-1">
          <button
            onClick={() => setShowRaw(false)}
            className={`px-3 py-1 text-xs font-bold rounded ${!showRaw ? 'bg-ctp-surface2 text-white' : 'text-ctp-subtext0 hover:text-ctp-text'}`}
          >
            Conversation
          </button>
          <button
            onClick={() => setShowRaw(true)}
            className={`px-3 py-1 text-xs font-bold rounded ${showRaw ? 'bg-ctp-surface2 text-white' : 'text-ctp-subtext0 hover:text-ctp-text'}`}
          >
            Raw JSON
          </button>
        </div>
      </div>

      {/* Conversation Scroll Area */}
      <div className="flex-1 overflow-y-auto p-8">
        <div className="max-w-3xl mx-auto space-y-8">
          {showRaw ? (
            <pre className="bg-ctp-surface0 border border-ctp-surface1 rounded-xl p-6 text-xs font-mono text-ctp-subtext1 overflow-x-auto">
              {JSON.stringify(currentRun.messages, null, 2)}
            </pre>
          ) : (
            <>
              {currentRun.messages
                .filter(msg => msg.role !== 'tool')
                .map((msg, i, arr) => (
                <MessageBubble
                  key={i}
                  message={msg}
                  toolResultMap={toolResultMap}
                  isLast={i === arr.length - 1 && currentRun.state === 'completed'}
                />
              ))}
            </>
          )}
          <div ref={endRef} />
        </div>

        {messageCount === 0 && !showRaw && (
          <div className="text-center text-sm text-ctp-subtext0 mt-12">No messages recorded for this run.</div>
        )}
      </div>

      {/* Footer (mock input) */}
      <div className="bg-ctp-base border-t border-ctp-surface1 p-4">
        <div className="max-w-3xl mx-auto flex gap-3">
          <div className="flex-1 bg-ctp-crust border border-ctp-surface1 rounded-xl px-4 py-3 flex items-center gap-3">
            <span className="material-symbols-outlined text-ctp-subtext0">psychology</span>
            <span className="text-sm text-ctp-surface2">Agent conversation is read-only</span>
          </div>
        </div>
      </div>
    </div>
  )
}
