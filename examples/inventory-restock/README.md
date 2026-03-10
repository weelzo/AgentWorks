# Example: Inventory Restock

Demonstrates what happens when an agent encounters a **FATAL (Tier 3)** error — an unrecoverable failure that immediately terminates the run. Compare this with the [ecommerce-fulfillment](../ecommerce-fulfillment/) example where errors are handled gracefully.

## Scenario: Inventory Restock with Expired Credentials

> **Team:** Operations
> **Agent task:** "Check inventory for SKU-3001. If below threshold, place an emergency supplier order and notify the warehouse."

The agent will:
1. Check inventory levels for SKU-3001 — **succeeds** (3 units, threshold 25 → critical low)
2. Place a supplier order for 50 units — **HTTP 401** → "API key expired" → **FATAL**
3. ~~Notify warehouse~~ — **never reached** (run already failed)

### Why This is FATAL

The ErrorClassifier matches "401" in the error message against `TIER_3_PATTERNS["auth_failure"]`. Auth failures are **never retried** and **never recoverable** — the agent can't fix expired credentials. The run transitions immediately to `FAILED`.

---

## Quick Start

### 1. Start the Mock Supplier Server

```bash
uv run python examples/inventory-restock/mock_supplier_server.py
```

Starts on **port 8003**:
- `POST /api/inventory/levels` — Returns stock data (SKU-3001: 3 units, critical_low)
- `POST /api/supplier/order` — Always returns HTTP 401 (expired API key)
- `POST /api/warehouse/notify` — Would succeed, but agent never gets here

### 2. Register Tools

```bash
cd examples/inventory-restock

curl -s -X POST http://localhost:8000/api/v1/tools \
  -H "Content-Type: application/json" -d @tools/inventory_levels.json

curl -s -X POST http://localhost:8000/api/v1/tools \
  -H "Content-Type: application/json" -d @tools/supplier_order.json

curl -s -X POST http://localhost:8000/api/v1/tools \
  -H "Content-Type: application/json" -d @tools/restock_notify.json
```

### 3. Run the Agent

```bash
curl -s -X POST http://localhost:8000/api/v1/runs \
  -H "Content-Type: application/json" \
  -d @run_request.json | python3 -m json.tool
```

---

## Expected Result

```
state:       failed
iterations:  1-2
tool_calls:  2 (inventory_levels[success], supplier_order[FATAL])
error:       "Client error: HTTP 401: ..."
```

### Expected Flow

```
Iteration 1:
  [PLANNING]       Agent checks inventory
  [EXECUTING_TOOL] inventory_levels(SKU-3001) → success (3 units, critical_low)
  [REFLECTING]     Stock is critically low, need to reorder

Iteration 2:
  [PLANNING]       Agent places supplier order
  [EXECUTING_TOOL] supplier_order(SKU-3001, qty=50) → HTTP 401 → FATAL
  [FAILED]         Run terminates immediately — auth_failure classified as Tier 3
```

The third tool (`restock_notify`) is registered but **never called** because the run halts at the FATAL error.

---

## What to Look for in the Dashboard

### Timeline View (`1`)
- **Error Recovery Summary** shows "1 fatal error (unrecoverable)"
- Red error banner with the full error message
- `supplier_order` card: **red border**, **FATAL** tier badge, error details
- Run ends with "Run Failed" marker (red dot) instead of green "Run Finished"

### State Machine View (`2`)
- The `executing_tool → failed` edge lights up with `fatal_error` trigger
- The `completed` state is **never visited** — the path terminates at `failed`
- Compare with ecommerce-fulfillment where the path loops back through `reflecting → planning`

### Conversation View (`4`)
- The agent's last message is cut short — no final summary
- `supplier_order` result shows **FATAL** badge in red
- Stats bar shows the error count

### Cost Dashboard (`3`)
- Fewer iterations = lower cost than the error-recovery demo
- ExecutionLog shows `supplier_order — FATAL`
- The agent spent tokens on planning the restock but never completed it

---

## Comparison: All Three Examples

| Metric | Customer Support | E-Commerce Fulfillment | Inventory Restock |
|--------|-----------------|----------------|-------------|
| Final state | completed | completed | **failed** |
| Iterations | 2 | 3 | 1-2 |
| Tool calls | 3 | 4 | 2 |
| Errors | 0 | 2 | 1 |
| Error tiers | — | RETRYABLE + RECOVERABLE | **FATAL** |
| Agent completed task? | Yes | Yes (with recovery) | **No** |
| Dashboard state path | → completed | → reflecting → planning (loop) | → **failed** |

---

## File Reference

```
examples/inventory-restock/
  README.md                   # This file
  run_request.json            # Agent run request
  mock_supplier_server.py     # FastAPI mock on port 8003 (401 on supplier)
  tools/
    inventory_levels.json     # Always succeeds
    supplier_order.json       # retry_policy: 0 retries (auth errors are fatal)
    restock_notify.json       # Never reached (run fails before this)
```
