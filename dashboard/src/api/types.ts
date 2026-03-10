/** Mirrors Python RunResponse model */
export interface RunData {
  run_id: string
  agent_id: string
  team_id: string
  state: AgentState
  outcome: string | null
  iteration_count: number
  total_cost_usd: number
  total_tokens: number
  duration_ms: number | null
  max_budget_usd: number | null
  max_iterations: number | null
  error: string | null
  messages: Message[]
  tool_calls: ToolCallRecord[]
  state_history: StateTransition[]
  token_usage: TokenUsage
  created_at: string | null
  completed_at: string | null
}

/** Error summary for run listings */
export interface ErrorSummary {
  retryable: number
  recoverable: number
  fatal: number
}

/** Mirrors Python RunListItem model */
export interface RunListItem {
  run_id: string
  agent_id: string
  team_id: string
  state: string
  outcome: string | null
  total_cost_usd: number
  total_tokens: number
  iteration_count: number
  created_at: string | null
  error_summary?: ErrorSummary
}

export type AgentState =
  | 'idle'
  | 'planning'
  | 'executing_tool'
  | 'awaiting_llm'
  | 'reflecting'
  | 'completed'
  | 'failed'
  | 'suspended'

export interface Message {
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: string | null
  tool_calls?: ToolCallRef[]
  tool_call_id?: string
  name?: string
  timestamp: string
}

export interface ToolCallRef {
  id: string
  type: string
  function: { name: string; arguments: string }
}

export interface ToolCallRecord {
  tool_call_id: string
  tool_name: string
  input_data: Record<string, unknown>
  output_data: Record<string, unknown> | null
  error: string | null
  started_at: string
  completed_at: string | null
  duration_ms: number | null
  retry_count: number
}

export interface StateTransition {
  from: string
  to: string
  trigger: string
  timestamp: string
  guard_result?: boolean
  duration_ms?: number
}

export interface TokenUsage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  estimated_cost_usd: number
}

export interface HealthResponse {
  status: string
  version: string
  uptime_seconds: number
  checks: Record<string, string>
}

/** Error tier classification for tool calls */
export type ErrorTier = 'retryable' | 'recoverable' | 'fatal' | null

/** Grouped iteration for the timeline view */
export interface IterationToolCall {
  name: string
  input: Record<string, unknown>
  output: Record<string, unknown> | null
  error: string | null
  duration_ms: number | null
  retry_count: number
  error_tier: ErrorTier
}

export interface Iteration {
  index: number
  planning: {
    content: string | null
    tokens: number
    cost: number
    timestamp: string
  } | null
  toolCall: IterationToolCall | null
  reflection: {
    content: string | null
    tokens: number
    cost: number
    timestamp: string
  } | null
  totalCost: number
  totalTokens: number
  duration_ms: number
  additionalToolCalls?: IterationToolCall[]
}
