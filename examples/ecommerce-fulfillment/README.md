# Example: E-Commerce Order Fulfillment

Demonstrates the 3-tier error classification system that drives AgentWorks from 85% to 99.2% success rate. This example deliberately triggers **retryable** and **recoverable** errors so you can watch the recovery flow in the dashboard.

## The 3-Tier Error System

| Tier | Type | What Happens | Agent Sees It? |
|------|------|-------------|----------------|
| 1 | **RETRYABLE** | Auto-retry with exponential backoff | No — transparent |
| 2 | **RECOVERABLE** | Error + hint fed back to LLM, agent self-corrects | Yes — as system message |
| 3 | **FATAL** | Run transitions to FAILED immediately | N/A — run stops |

## Scenario: E-Commerce Order Fulfillment

> **Team:** Operations
> **Agent task:** "Process order #ORD-7742 for customer Maya Torres. Check inventory for item SKU-2048, process the $129.99 payment, and create a shipping label."

The agent encounters **two realistic failures**:

1. **Payment service returns 503** (RETRYABLE) — The tool registry retries automatically with exponential backoff. The agent never sees this error. The tool succeeds on the 2nd attempt. Visible in the dashboard as `retry_count: 1` on the tool call record.

2. **Shipping label fails with "invalid_input"** (RECOVERABLE) — The `shipping_create` tool schema doesn't list `weight_kg` as a parameter at all, so the LLM omits it. But the API requires it and returns a validation error. The error classifier feeds this back to the LLM with a recovery hint. The LLM self-corrects — it recalls `weight_kg: 0.34` from the earlier inventory response and retries with the correct input.

---

## Quick Start

### Prerequisites

Same as the [customer-support-agent example](../customer-support-agent/README.md#prerequisites):
- Python 3.12+, [uv](https://github.com/astral-sh/uv)
- Redis running locally
- PostgreSQL with `agentworks` database
- An OpenAI API key configured in `config/local-dev.yaml`

### 1. Start the Mock Flaky Server

```bash
# From the project root
uv run python examples/ecommerce-fulfillment/mock_flaky_server.py
```

Starts on **port 8002** with three endpoints:
- `POST /api/inventory/check` — Always succeeds (SKU-2048: Wireless Bluetooth Headphones, 47 in stock)
- `POST /api/payments/process` — Returns 503 on first attempt per order_id, succeeds after
- `POST /api/shipping/create` — Rejects if `weight_kg` is missing, succeeds with it
- `POST /reset` — Clears failure counters (for re-running the demo)

### 2. Start the AgentWorks Runtime

In a separate terminal:

```bash
AGENTWORKS_CONFIG_PATH=config/local-dev.yaml uv run uvicorn agentworks.api:app --host 0.0.0.0 --port 8000
```

### 3. Register Tools

```bash
cd examples/ecommerce-fulfillment

curl -s -X POST http://localhost:8000/api/v1/tools \
  -H "Content-Type: application/json" -d @tools/inventory_check.json

curl -s -X POST http://localhost:8000/api/v1/tools \
  -H "Content-Type: application/json" -d @tools/payment_process.json

curl -s -X POST http://localhost:8000/api/v1/tools \
  -H "Content-Type: application/json" -d @tools/shipping_create.json
```

### 4. Run the Agent

```bash
curl -s -X POST http://localhost:8000/api/v1/runs \
  -H "Content-Type: application/json" \
  -d @run_request.json | python3 -m json.tool
```

### 5. Re-running

Reset the mock server's failure counters before each run:

```bash
curl -s -X POST http://localhost:8002/reset
```

---

## Expected Result

The agent completes in ~3 iterations with 4 tool calls:

```
state:       completed
iterations:  3
tool_calls:  4 (inventory_check, payment_process, shipping_create[fail], shipping_create[success])
cost:        ~$0.012
```

### Expected Flow

```
Iteration 1:
  [PLANNING]       Agent decides to check inventory and process payment
  [EXECUTING_TOOL] inventory_check(SKU-2048) → success (in stock, weight: 0.34kg)
  [EXECUTING_TOOL] payment_process($129.99) → 503 auto-retry → success (retry_count=1)
  [REFLECTING]     Both tools succeeded

Iteration 2:
  [PLANNING]       Agent decides to create shipping label
  [EXECUTING_TOOL] shipping_create(missing weight_kg) → RECOVERABLE error
  [REFLECTING]     Error fed back: "Missing required field: weight_kg"

Iteration 3:
  [PLANNING]       Agent self-corrects, adds weight_kg=0.34
  [EXECUTING_TOOL] shipping_create(with weight_kg) → success
  [REFLECTING]     Shipping label created, agent summarizes result

Final:
  [COMPLETED]      Order fulfilled: inventory confirmed, payment processed,
                   shipping label SHP-XXXXX created
```

---

## What to Look for in the Dashboard

Start the dashboard (`cd dashboard && npm run dev`) and paste the `run_id`.

### Timeline View (`1`)
- **3 iterations** (vs 2 for the happy-path customer-support example)
- Iteration 2 shows a red error border on the `shipping_create` tool call
- Expand iteration 1's `payment_process` card to see `retry_count: 1`

### State Machine View (`2`)
- The `executing_tool → reflecting → planning` loop appears **twice**:
  - Once for the normal flow (iteration 1 → 2)
  - Once for the recovery flow (iteration 2 → 3)

### Conversation View (`4`)
- A **system message** appears between iterations 2 and 3 with the recovery hint:
  *"Error occurred: Missing required field: weight_kg. Please review the tool's input schema and correct your input."*
- The agent's next message shows it adding `weight_kg: 0.34` from the inventory result

### Cost Dashboard (`3`)
- The extra iteration is visible in the cost waterfall
- Compare with the customer-support run to see the recovery cost overhead

---

## Comparison: Happy Path vs E-Commerce Fulfillment

| Metric | Customer Support (happy) | E-Commerce Fulfillment |
|--------|-------------------------|----------------|
| Iterations | 2 | 3 |
| Tool calls | 3 | 4 (1 failed + retried) |
| Errors encountered | 0 | 2 (1 retryable, 1 recoverable) |
| Final state | completed | completed |
| Error tiers exercised | none | RETRYABLE + RECOVERABLE |

---

## How It Works Under the Hood

```
IDLE ──start──> PLANNING
  └──> AWAITING_LLM ──llm_responded──> PLANNING
  └──> EXECUTING_TOOL (inventory_check + payment_process)
       payment_process: HTTP 503 → retry_policy kicks in → 2nd attempt succeeds
       ──tool_done──> REFLECTING

  └──> PLANNING (LLM decides to create shipping label)
  └──> AWAITING_LLM ──llm_responded──> PLANNING
  └──> EXECUTING_TOOL (shipping_create — missing weight_kg)
       ErrorClassifier: "invalid_input" → RECOVERABLE
       Recovery hint injected as system message
       ──tool_error──> REFLECTING

  └──> PLANNING (LLM reads error, self-corrects with weight_kg=0.34)
  └──> AWAITING_LLM ──llm_responded──> PLANNING
  └──> EXECUTING_TOOL (shipping_create — with weight_kg) ──tool_done──> REFLECTING
  └──> COMPLETED
```

Key code paths:
- **Retry logic:** `src/agentworks/tool_registry.py` — HTTP 503 matched by `RetryPolicy.is_retryable("server_error")`
- **Error classification:** `src/agentworks/errors.py` — `ErrorClassifier.classify()` matches `"invalid_input"` → `RECOVERABLE`
- **Recovery injection:** `src/agentworks/engine.py:339-427` — error message + hint appended to `ctx.messages`

---

## File Reference

```
examples/ecommerce-fulfillment/
  README.md                   # This file
  run_request.json            # Agent run request body
  mock_flaky_server.py        # FastAPI mock with intentional failures (port 8002)
  tools/
    inventory_check.json      # Always succeeds — returns stock + weight
    payment_process.json      # Explicit retry_policy for 503 auto-retry
    shipping_create.json      # weight_kg omitted from schema (triggers RECOVERABLE)
```
