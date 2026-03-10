# AgentWorks Architecture

## The Problem

Multiple product teams. Multiple independent LLM wrappers. Thousands of lines of duplicated glue code. Zero shared observability. Unattributed LLM spend.

A retry loop generates millions of requests during an API outage. Agent failures are silently swallowed. Nobody can answer: "How much does this agent cost per run?"

AgentWorks was built to replace all of that with a single runtime where every state transition is explicit, observable, and recoverable.

---

## System Overview

```
  Client Request
       │
       ▼
┌──────────────────────────────────────────────┐
│              FastAPI + Middleware             │
│  ┌──────┐ ┌──────┐ ┌───────┐ ┌───────────┐  │
│  │ Auth │ │ CORS │ │ Rate  │ │ Body Size │  │
│  │      │ │      │ │ Limit │ │   Guard   │  │
│  └──────┘ └──────┘ └───────┘ └───────────┘  │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│             Execution Engine                 │
│                                              │
│  Orchestrates each run through the state     │
│  machine. Coordinates LLM calls, tool        │
│  execution, memory assembly, and             │
│  checkpointing. Enforces budget guards       │
│  and iteration limits.                       │
│                                              │
│  Key classes: ExecutionEngine,               │
│  ExecutionContext                             │
└──┬───────────┬───────────┬───────────────────┘
   │           │           │
   ▼           ▼           ▼
┌────────┐ ┌────────┐ ┌────────────┐
│ State  │ │  LLM   │ │   Tool     │
│Machine │ │Gateway │ │  Registry  │
└───┬────┘ └───┬────┘ └─────┬──────┘
    │          │             │
    ▼          ▼             ▼
┌────────┐ ┌────────┐ ┌────────────┐
│Memory  │ │Observ- │ │ Checkpoint │
│Manager │ │ability │ │  Manager   │
└────────┘ └────────┘ └─────┬──────┘
                            │
                     ┌──────┴──────┐
                     ▼             ▼
                ┌────────┐   ┌──────────┐
                │ Redis  │   │PostgreSQL│
                │ (hot)  │   │  (cold)  │
                └────────┘   └──────────┘
```

---

## State Machine

The core of AgentWorks is a deterministic finite state machine with 8 states and 18 transitions. Every agent run moves through these states — no implicit behavior, no hidden loops.

### Why a Custom State Machine?

We evaluated three options:

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| LangGraph | Large ecosystem, community | Coupled to LangChain, opaque internals, breaking API changes between versions | Rejected |
| Temporal/Prefect | Battle-tested workflow engines | Heavy infrastructure dependency, overkill for our state model | Rejected |
| **Custom FSM** | Full control, explicit transitions, lightweight, debuggable | Must build and maintain ourselves | **Chosen** |

The deciding factor: we needed every transition to be observable, checkpointable, and replayable. Framework abstractions hid too much.

### State Diagram

```
                        ┌────────┐
                        │  IDLE  │
                        └───┬────┘
                            │ start
                            ▼
                      ┌───────────┐
             ┌───────>│ PLANNING  │<──────────────┐
             │        └─────┬─────┘               │
             │              │                     │
             │        ┌─────┼─────┐               │
             │        │     │     │               │
             │   needs_tool │  has_answer    llm_responded
             │        │     │     │               │
             │        ▼     │     ▼               │
             │  ┌───────────┤ ┌──────────┐  ┌──────────┐
    resume   │  │ EXECUTING │ │COMPLETED │  │ AWAITING │
             │  │   _TOOL   │ └──────────┘  │   _LLM   │
             │  └─────┬─────┘               └─────┬────┘
             │        │                           │
             │   tool_done /                   llm_error
             │   tool_error                       │
             │        │                           │
             │        ▼                           │
             │  ┌───────────┐                     │
             │  │REFLECTING │                     │
             │  └─────┬─────┘                     │
             │        │                           │
             │   continue /                       │
             │   has_answer                       │
             │        │                           │
     ┌───────┴──┐     │                           │
     │SUSPENDED │<────┘                           │
     └────┬─────┘  (budget_exceeded)              │
          │                                       │
        abort                                     │
          │                                       │
          ▼                                       ▼
     ┌──────────┐                           ┌──────────┐
     │  FAILED  │                           │  FAILED  │
     └──────────┘                           └──────────┘
```

### States and Their Purpose

| State | What happens | Transitions out |
|-------|-------------|-----------------|
| **IDLE** | Run created, waiting to start | start → PLANNING |
| **PLANNING** | LLM decides next action (tool call or final answer) | needs_tool, has_answer, budget_exceeded, error |
| **AWAITING_LLM** | Waiting for LLM provider response | llm_responded → PLANNING, llm_error → FAILED |
| **EXECUTING_TOOL** | Running a tool via the registry | tool_done / tool_error → REFLECTING |
| **REFLECTING** | Processing tool results, deciding whether to continue | continue → PLANNING, has_answer → COMPLETED |
| **SUSPENDED** | Budget exceeded or manually paused; resumable | resume → PLANNING, abort → FAILED |
| **COMPLETED** | Final answer delivered, checkpoint promoted to cold store | Terminal |
| **FAILED** | Unrecoverable error | Terminal |

### Guards

Two guards gate transitions to prevent runaway agents:

- **check_iteration_limit** — gates PLANNING → EXECUTING_TOOL and REFLECTING → PLANNING. Prevents infinite tool-call loops.
- **check_budget** — gates PLANNING → SUSPENDED and REFLECTING → SUSPENDED. Suspends the run (not kills) when cost exceeds the budget, allowing manual resume.

Key classes: `StateMachine`, `AgentState`, `StateTransition`, `ExecutionContext`

---

## Tool Registry

### Design Decision: Self-Service Over Centralized

In a typical setup, adding a tool means modifying runtime source code. The platform team becomes a bottleneck.

AgentWorks uses a **self-service registry**: product teams register their own tools via API with JSON Schema validation, versioning, and health monitoring. A single `curl` command is all it takes.

### How It Works

```
  Product Team                    AgentWorks                    Tool Service
       │                              │                              │
       │  POST /tools (register)      │                              │
       │─────────────────────────────>│                              │
       │  { name, endpoint_url,       │                              │
       │    input_schema,             │                              │
       │    output_schema }           │                              │
       │                              │                              │
       │                              │  During agent execution:     │
       │                              │                              │
       │                              │  1. Validate input (schema)  │
       │                              │  2. Check rate limit         │
       │                              │  3. SSRF protection check    │
       │                              │  4. HTTP call ──────────────>│
       │                              │  5. Validate output (schema) │
       │                              │  6. Record latency metric    │
       │                              │<─────────── response ────────│
```

### Security: SSRF Protection

Tools execute HTTP calls to external endpoints. Without protection, a malicious tool definition could target internal services. AgentWorks blocks requests to private network ranges (localhost, 10.x, 172.16-31.x, 192.168.x, 169.254.x metadata endpoint) before any HTTP call is made.

Key classes: `ToolRegistry`, `ToolDefinition`, `ToolResult`, `TokenBucket`

---

## Checkpointing

### Design Decision: Dual-Store Strategy

We needed sub-2ms checkpoint writes (to avoid slowing agent runs) AND durable long-term storage (for replay and audit). No single store satisfies both.

| Option | Write latency | Durability | Cost at scale | Decision |
|--------|:---:|:---:|:---:|---|
| Redis only | ~0.8ms | Volatile | High (RAM) | Rejected |
| PostgreSQL only | ~5ms | Durable | Low | Rejected — too slow for every transition |
| **Redis (hot) → PostgreSQL (cold)** | ~0.8ms | Durable after promotion | Optimal | **Chosen** |

### How It Works

```
  Every state transition                   On run completion
         │                                       │
         ▼                                       ▼
   ┌───────────┐                          ┌───────────┐
   │   Redis   │  ── promote_to_cold ──>  │ PostgreSQL│
   │  (hot)    │       (async)            │  (cold)   │
   │  ~0.8ms   │                          │  durable  │
   │  TTL: 24h │                          │  forever  │
   └───────────┘                          └───────────┘
```

- **Active runs** checkpoint to Redis on every state transition (~0.8ms, 15 checkpoints per typical run = ~12ms total overhead)
- **Completed runs** are promoted to PostgreSQL for long-term storage and audit
- **Crash recovery**: if the runtime restarts, active runs resume from the last Redis checkpoint
- **Error handling**: if PostgreSQL write fails, Redis data is preserved (not deleted)

Key classes: `CheckpointManager`, `CheckpointData`

---

## LLM Gateway

### Design Decision: Custom Gateway Over LiteLLM

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| LiteLLM | 100+ providers, mature | Another dependency, limited cost tracking control, circuit breaker doesn't match our needs | Rejected |
| **Custom gateway** | Full control over retries, cost tracking, circuit breaker, token counting | Must maintain provider adapters ourselves | **Chosen** |

### Multi-Provider Architecture

```
                    ┌───────────────────┐
                    │    LLM Gateway    │
                    │                   │
                    │  ┌─────────────┐  │
                    │  │   Router    │  │
                    │  │  (primary + │  │
                    │  │  fallback)  │  │
                    │  └──────┬──────┘  │
                    │         │         │
                    │    ┌────┼────┐    │
                    │    ▼         ▼    │
                    │ ┌──────┐ ┌──────┐ │
                    │ │OpenAI│ │Claude│ │
                    │ │      │ │      │ │
                    │ └──┬───┘ └──┬───┘ │
                    │    │        │     │
                    │ Circuit  Circuit   │
                    │ Breaker  Breaker   │
                    └────┴────────┴─────┘
```

- **Circuit breaker** per provider — after consecutive failures, traffic automatically shifts to the fallback provider (recovery in <2 seconds)
- **Token counting** with tiktoken — accurate to the token, not the ~30% error estimate of chars/4
- **Cost tracking** per run — every LLM call records prompt tokens, completion tokens, and USD cost

Key classes: `LLMGateway`, `LLMProvider`, `CompletionResponse`, `ToolCallResponse`

---

## Memory Manager

### Design Decision: Sliding Window + Vector Recall

The challenge: LLMs have finite context windows, but agents need access to earlier conversation turns and relevant long-term knowledge.

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| Truncation (drop old messages) | Simple | Loses critical context | Rejected |
| Summarization only | Preserves meaning | Lossy, adds LLM calls ($), latency | Rejected |
| **Sliding window + vector recall** | Recent context preserved, relevant older context retrieved on demand | More complex | **Chosen** |

```
  Context Assembly (before each LLM call)
  ────────────────────────────────────────
  ┌──────────────────────────────────────┐
  │  System prompt              200 tok  │
  ├──────────────────────────────────────┤
  │  Vector recall (3 relevant  800 tok  │
  │  memories from past runs)            │
  ├──────────────────────────────────────┤
  │  Sliding window (last 5    2000 tok  │
  │  conversation turns)                 │
  ├──────────────────────────────────────┤
  │  Available for response    9000 tok  │
  └──────────────────────────────────────┘
                        Total: 12000 tok budget
```

Key classes: `MemoryManager`, `MemoryEntry`, `SlidingWindowMemory`, `VectorMemory`

---

## Observability

Full OpenTelemetry integration — traces, metrics, and structured logging.

```
  Agent Run
     │
     ├── Trace span: run lifecycle
     │     ├── Span: planning (LLM call)
     │     ├── Span: tool execution
     │     ├── Span: reflecting
     │     └── Span: checkpoint save
     │
     ├── Metrics
     │     ├── runs_started_total (counter)
     │     ├── run_duration_ms (histogram)
     │     ├── llm_cost_usd_total (counter, per team)
     │     ├── tool_latency_ms (histogram, per tool)
     │     └── active_runs (gauge)
     │
     └── Structured logs (JSON)
           ├── state transitions
           ├── error classifications
           └── cost attribution
```

Key classes: `ObservabilityManager`, `ObservabilityConfig`

---

## Security Model

| Layer | Protection | How |
|-------|-----------|-----|
| **Authentication** | API key validation | Timing-safe comparison (`hmac.compare_digest`) |
| **Authorization** | Per-key rate limiting | Token bucket algorithm |
| **Network** | SSRF protection | Block private IP ranges before tool HTTP calls |
| **Input** | Body size limits | Reject oversized requests (configurable, default 1MB) |
| **Transport** | CORS | Config-driven origin allowlist |
| **Budget** | Cost guards | Per-run budget with automatic suspension |

Health endpoints (`/api/v1/health/*`) are exempt from authentication for Kubernetes probe compatibility.

---

## Design Goals

AgentWorks was designed to solve the problems that emerge when multiple teams run AI agents in production:

| Problem | Without AgentWorks | With AgentWorks |
|---------|-------------------|-----------------|
| Duplicated LLM glue code | Each team builds their own wrapper | Shared runtime, teams only register tools |
| Agent success rate | ~85% (errors crash the run) | 99%+ (3-tier error recovery with self-correction) |
| Diagnosing failures | Grep through raw logs | Dashboard + OpenTelemetry traces in minutes |
| Cost visibility | Unknown until the invoice | Per-run, per-team, per-model attribution |
| Adding a new tool | Modify runtime source code | Self-service API registration |
| Provider failover | Manual intervention | Automatic circuit breaker (<2s) |
| Crash recovery | Start over | Resume from last checkpoint |
