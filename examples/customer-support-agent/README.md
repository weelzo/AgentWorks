# Example: Customer Support Agent

A complete production scenario showing how a support team deploys an AI agent that looks up customer accounts, checks billing history, and creates refund tickets — all with full observability and a live dashboard.

## Scenario

> **Team:** Customer Support
> **Agent task:** "Customer #4821 says they were charged twice for their Pro plan upgrade last month. Look up their account, check the billing history, and create a refund ticket if the double charge is confirmed."

The agent will:
1. Look up customer #4821 in the CRM
2. Check their billing history for duplicate charges (often in parallel with step 1)
3. Confirm the double charge and create a refund ticket
4. Respond with a summary

---

## Quick Start (Local Development)

### Prerequisites

- Python 3.12+, [uv](https://github.com/astral-sh/uv)
- Redis running locally (`brew install redis && brew services start redis`)
- PostgreSQL running locally with a database created:
  ```bash
  createdb agentworks
  psql agentworks -c "CREATE USER agentworks WITH PASSWORD 'localdev'; GRANT ALL ON SCHEMA public TO agentworks;"
  ```
- An OpenAI API key

### 1. Configure

Edit `config/local-dev.yaml` at the project root. Set your OpenAI API key in the `api_key_ref` field:

```yaml
providers:
  - provider_id: openai-primary
    provider_type: openai
    base_url: "https://api.openai.com/v1"
    api_key_ref: "sk-proj-your-key-here"   # or "env:OPENAI_API_KEY"
    models:
      - model_id: gpt-4o
        # ...
```

### 2. Start the Mock Tools Server

The example includes a mock API server that simulates the CRM, billing, and ticketing systems with realistic data.

```bash
# From the project root
uv run python examples/customer-support-agent/mock_tools_server.py
```

This starts on port 8001 with three endpoints:
- `POST /api/customers/lookup` — returns customer profile (Sarah Chen, Pro plan)
- `POST /api/billing/history` — returns charges including the duplicate on Feb 15
- `POST /api/tickets/create` — creates a refund ticket and returns a ticket ID

### 3. Start the AgentWorks Runtime

In a separate terminal:

```bash
AGENTWORKS_CONFIG_PATH=config/local-dev.yaml uv run uvicorn agentworks.api:app --host 0.0.0.0 --port 8000
```

Verify health:

```bash
curl -s http://localhost:8000/api/v1/health | python3 -m json.tool
```

### 4. Register Tools

```bash
cd examples/customer-support-agent

curl -s -X POST http://localhost:8000/api/v1/tools \
  -H "Content-Type: application/json" -d @tools/customer_lookup.json

curl -s -X POST http://localhost:8000/api/v1/tools \
  -H "Content-Type: application/json" -d @tools/billing_history.json

curl -s -X POST http://localhost:8000/api/v1/tools \
  -H "Content-Type: application/json" -d @tools/create_ticket.json
```

Verify:

```bash
curl -s http://localhost:8000/api/v1/tools | python3 -m json.tool
```

### 5. Run the Agent

```bash
curl -s -X POST http://localhost:8000/api/v1/runs \
  -H "Content-Type: application/json" \
  -d @run_request.json | python3 -m json.tool
```

### Expected Result

The agent completes in ~2 iterations with 3 tool calls:

```
state:       completed
iterations:  2
tool_calls:  3 (customer_lookup, billing_history, create_ticket)
messages:    8
cost:        ~$0.008
tokens:      ~2400
```

The conversation flow:

```
[system]     You are a customer support agent...
[user]       Customer #4821 says they were charged twice...
[assistant]  → calls customer_lookup("4821") + billing_history("4821") in parallel
[tool]       Sarah Chen, Pro plan, active
[tool]       5 charges — including duplicate $49.99 on Feb 15
[assistant]  → calls create_ticket(refund, $49.99)
[tool]       Ticket TKT-XXXXXXXX created, assigned to billing-team
[assistant]  I confirmed Sarah Chen was charged twice for the Pro Plan upgrade
             on February 15, 2026. A refund ticket has been created...
```

**Copy the `run_id` from the response** — you'll use it to visualize the run.

---

## 6. Visualize in the Dashboard

### Start the Dashboard

In a third terminal:

```bash
cd dashboard
npm run dev
```

Opens at http://localhost:5173 (or the next available port).

### Load a Run

Paste the `run_id` into the input field in the header (or press `/` to focus it), then press Enter.

### Five Views

Switch between views using the sidebar or keyboard shortcuts `1`-`5`:

| Key | View | What You See |
|-----|------|-------------|
| `1` | **Run Timeline** | Vertical timeline of iterations — expand each card to see LLM reasoning, tool I/O, and timestamps |
| `2` | **State Machine** | Animated SVG diagram showing the path through IDLE → PLANNING → EXECUTING_TOOL → REFLECTING → COMPLETED |
| `3` | **Cost Dashboard** | Cost waterfall per iteration, token breakdown (prompt vs completion), budget burn rate, tool frequency chart |
| `4` | **Conversation** | Chat-style message thread with role-colored bubbles, inline tool call results, and raw JSON toggle |
| `5` | **Run Comparison** | Load two run IDs side-by-side to compare stats, timelines, and find where they diverged |

### Keyboard Shortcuts

- `1`-`5` — switch views
- `/` — focus run ID input
- `[` — toggle sidebar

---

## 7. Retrieve a Run Later

Completed runs are persisted. Fetch them anytime:

```bash
curl -s http://localhost:8000/api/v1/runs/{run_id} | python3 -m json.tool
```

List recent runs:

```bash
curl -s "http://localhost:8000/api/v1/runs?limit=10" | python3 -m json.tool
```

---

## What Happened Under the Hood

```
IDLE ──start──> PLANNING
  └──> AWAITING_LLM ──llm_responded──> PLANNING
  └──> EXECUTING_TOOL (customer_lookup + billing_history in parallel)
       ──tool_done──> REFLECTING
  └──> PLANNING (LLM confirms double charge, creates ticket)
  └──> AWAITING_LLM ──llm_responded──> PLANNING
  └──> EXECUTING_TOOL (create_ticket) ──tool_done──> REFLECTING
  └──> PLANNING (LLM generates final answer)
  └──> COMPLETED (checkpoint promoted to PostgreSQL)

State transitions: 14   |   Checkpoints saved: 14   |   Tool calls: 3
```

---

## File Reference

```
examples/customer-support-agent/
  README.md                # This file
  run_request.json         # Agent run request body
  mock_tools_server.py     # Local mock API for CRM, billing, ticketing
  config.yaml              # Runtime config for this scenario
  tools/
    customer_lookup.json   # CRM lookup tool definition
    billing_history.json   # Billing history tool definition
    create_ticket.json     # Ticket creation tool definition
```
