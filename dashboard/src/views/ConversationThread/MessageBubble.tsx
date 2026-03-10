import { useState } from 'react'
import type { Message, ToolCallRecord, ToolCallRef } from '../../api/types'
import { formatTimestamp } from '../../utils/format'

interface MessageBubbleProps {
  message: Message
  toolResultMap: Map<string, ToolCallRecord>
  isLast?: boolean
}

export function MessageBubble({ message, toolResultMap, isLast }: MessageBubbleProps) {
  if (message.role === 'system') return <SystemMessage message={message} />
  if (message.role === 'user') return <UserMessage message={message} />
  if (message.role === 'tool') return <ToolResultMessage message={message} />
  return <AssistantMessage message={message} toolResultMap={toolResultMap} isLast={isLast} />
}

function SystemMessage({ message }: { message: Message }) {
  const [expanded, setExpanded] = useState(false)
  const content = message.content ?? ''
  const isLong = content.length > 200

  return (
    <div className="flex justify-center">
      <div className="bg-ctp-surface0/30 border border-ctp-surface1 rounded-xl px-6 py-3 max-w-[90%] text-center">
        <div className="flex items-center justify-center gap-2 text-ctp-subtext0 mb-1">
          <span className="material-symbols-outlined text-sm">terminal</span>
          <span className="text-xs font-bold uppercase tracking-widest">System Prompt</span>
        </div>
        <p className="text-sm text-ctp-subtext0 italic leading-relaxed">
          {isLong && !expanded ? content.slice(0, 200) + '...' : content}
          {isLong && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-ctp-blue hover:underline ml-1"
            >
              {expanded ? 'Show less' : 'Show more'}
            </button>
          )}
        </p>
      </div>
    </div>
  )
}

function UserMessage({ message }: { message: Message }) {
  return (
    <div className="flex flex-col items-end">
      <div className="max-w-[85%] bg-ctp-blue/10 border border-ctp-blue/30 rounded-2xl rounded-tr-none px-5 py-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-bold text-ctp-blue uppercase tracking-wider">
            User{message.name ? ` (${message.name})` : ''}
          </span>
          {message.timestamp && (
            <span className="text-[10px] text-ctp-subtext0">{formatTimestamp(message.timestamp)}</span>
          )}
        </div>
        {message.content && (
          <p className="text-[15px] leading-relaxed text-ctp-text">{message.content}</p>
        )}
      </div>
    </div>
  )
}

function AssistantMessage({ message, toolResultMap, isLast }: {
  message: Message
  toolResultMap: Map<string, ToolCallRecord>
  isLast?: boolean
}) {
  const hasToolCalls = message.tool_calls && message.tool_calls.length > 0

  return (
    <>
      <div className="flex flex-col items-start">
        <div className={`max-w-[90%] w-full rounded-2xl rounded-tl-none px-5 py-4 space-y-4 ${
          isLast
            ? 'bg-ctp-surface0/60 border border-ctp-surface1 shadow-xl'
            : 'bg-ctp-surface0/40 border border-ctp-surface1'
        }`}>
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold text-ctp-text uppercase tracking-wider">
              {isLast ? 'Assistant (Final Answer)' : 'Assistant'}
            </span>
            {message.timestamp && (
              <span className="text-[10px] text-ctp-subtext0">{formatTimestamp(message.timestamp)}</span>
            )}
          </div>

          {message.content && (
            <div className="text-[15px] leading-relaxed text-ctp-subtext1">
              {message.content}
            </div>
          )}

          {/* Inline tool call blocks */}
          {hasToolCalls && (
            <div className={`grid gap-3 ${message.tool_calls!.length > 1 ? 'grid-cols-1 md:grid-cols-2' : 'grid-cols-1'}`}>
              {message.tool_calls!.map((tc: ToolCallRef, i: number) => {
                const tcAny = tc as unknown as Record<string, unknown>
                const id = tc.id ?? (tcAny.tool_call_id as string)
                const record = id ? toolResultMap.get(id) : undefined
                return <ToolCallBlock key={i} toolCall={tc} record={record} />
              })}
            </div>
          )}
        </div>
      </div>

      {/* Tool results below assistant message, indented */}
      {hasToolCalls && (
        <div className="pl-8 border-l-2 border-ctp-surface1 space-y-4">
          {message.tool_calls!.map((tc: ToolCallRef, i: number) => {
            const tcAny = tc as unknown as Record<string, unknown>
            const id = tc.id ?? (tcAny.tool_call_id as string)
            const result = id ? toolResultMap.get(id) : undefined
            if (!result) return null
            return <ToolResultBlock key={id ?? i} result={result} />
          })}
        </div>
      )}
    </>
  )
}

function ToolResultMessage({ message }: { message: Message }) {
  // Detect if the tool result content contains an error
  let isError = false
  try {
    const parsed = message.content ? JSON.parse(message.content) : null
    if (parsed?.error || parsed?.error_type) isError = true
  } catch { /* not JSON, treat as success */ }

  const colorClass = isError ? 'ctp-yellow' : 'ctp-green'

  return (
    <div className="pl-8 border-l-2 border-ctp-surface1">
      <div className={`bg-${colorClass}/5 border border-${colorClass}/20 rounded-xl p-4`}>
        <div className="flex items-center gap-2 mb-2">
          <span className={`material-symbols-outlined text-${colorClass} text-sm`}>
            {isError ? 'warning' : 'database'}
          </span>
          <span className={`text-xs font-bold text-${colorClass} uppercase tracking-wider`}>
            {isError ? 'Tool Error' : 'Tool Result'}{message.name ? `: ${message.name}` : ''}
          </span>
          {isError && (
            <span className={`bg-${colorClass}/15 border border-${colorClass}/30 rounded px-1.5 py-0.5 text-[10px] font-bold text-${colorClass}`}>
              RECOVERABLE
            </span>
          )}
        </div>
        {message.content && (
          <pre className={`font-mono text-xs text-${colorClass}/80 overflow-x-auto whitespace-pre-wrap`}>
            {tryFormatJson(message.content)}
          </pre>
        )}
      </div>
    </div>
  )
}

function ToolCallBlock({ toolCall, record }: { toolCall: ToolCallRef; record?: ToolCallRecord }) {
  // Handle both OpenAI format (function.name) and AgentWorks format (tool_name)
  const tc = toolCall as unknown as Record<string, unknown>
  const name = toolCall.function?.name
    ?? record?.tool_name
    ?? (tc.tool_name as string)
    ?? 'unknown'

  // Get args: prefer full record's input_data, fall back to inline function.arguments
  const args = record?.input_data
    ? JSON.stringify(record.input_data, null, 2)
    : toolCall.function?.arguments
      ? tryFormatJson(toolCall.function.arguments)
      : (tc.input_data ? JSON.stringify(tc.input_data, null, 2) : '{}')

  return (
    <div className="bg-ctp-yellow/5 border border-ctp-yellow/20 rounded-lg p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-ctp-yellow text-sm">function</span>
          <span className="text-xs font-mono font-semibold text-ctp-yellow">{name}</span>
        </div>
      </div>
      <div className="font-mono text-[12px] text-ctp-yellow/90 leading-snug">{args}</div>
    </div>
  )
}

function ToolResultBlock({ result }: { result: ToolCallRecord }) {
  const outputData = result.output_data as Record<string, unknown> | null
  const isRecoverableError = !result.error && Boolean(outputData?.error_type || outputData?.error)

  const icon = result.error ? 'error' : isRecoverableError ? 'warning' : 'database'
  const iconColor = result.error ? 'text-ctp-red' : isRecoverableError ? 'text-ctp-yellow' : 'text-ctp-green'
  const bgColor = result.error
    ? 'bg-ctp-red/5 border-ctp-red/20'
    : isRecoverableError
      ? 'bg-ctp-yellow/5 border-ctp-yellow/20'
      : 'bg-ctp-green/5 border-ctp-green/20'
  const textColor = result.error ? 'text-ctp-red/80' : isRecoverableError ? 'text-ctp-yellow/80' : 'text-ctp-green/80'

  return (
    <div className={`${bgColor} border rounded-xl p-4`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`material-symbols-outlined text-sm ${iconColor}`}>{icon}</span>
          <span className={`text-xs font-bold uppercase tracking-wider ${iconColor}`}>
            Tool Result: {result.tool_name}
          </span>
          {result.retry_count > 0 && (
            <span className="bg-ctp-peach/15 border border-ctp-peach/30 rounded px-1.5 py-0.5 text-[10px] font-bold text-ctp-peach flex items-center gap-1">
              <span className="material-symbols-outlined text-[10px]">replay</span>
              retried {result.retry_count}x
            </span>
          )}
          {isRecoverableError && (
            <span className="bg-ctp-yellow/15 border border-ctp-yellow/30 rounded px-1.5 py-0.5 text-[10px] font-bold text-ctp-yellow">
              RECOVERABLE
            </span>
          )}
          {result.error && (
            <span className="bg-ctp-red/15 border border-ctp-red/30 rounded px-1.5 py-0.5 text-[10px] font-bold text-ctp-red">
              FATAL
            </span>
          )}
        </div>
        {result.duration_ms != null && (
          <span className="text-[10px] text-ctp-subtext0">{(result.duration_ms / 1000).toFixed(1)}s execution</span>
        )}
      </div>
      {result.error ? (
        <pre className="font-mono text-xs text-ctp-red/80 overflow-x-auto whitespace-pre-wrap">{result.error}</pre>
      ) : result.output_data ? (
        <pre className={`font-mono text-xs ${textColor} overflow-x-auto whitespace-pre-wrap`}>
          {JSON.stringify(result.output_data, null, 2)}
        </pre>
      ) : null}
    </div>
  )
}

function tryFormatJson(str: string): string {
  try {
    return JSON.stringify(JSON.parse(str), null, 2)
  } catch {
    return str
  }
}
