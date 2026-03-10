"""
Mock Tools Server for the Customer Support Agent example.

Simulates the CRM, billing, and ticketing systems with realistic data.
Run this alongside the AgentWorks runtime to test the full agent loop.

Usage:
    uv run python examples/customer-support-agent/mock_tools_server.py
"""

import uuid
from datetime import datetime, timezone
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Any

app = FastAPI(title="Mock Tools Server")


# --------------------------------------------------------------------------
# Customer Lookup
# --------------------------------------------------------------------------

CUSTOMERS = {
    "4821": {
        "customer_id": "4821",
        "name": "Sarah Chen",
        "email": "sarah.chen@example.com",
        "plan": "Pro",
        "status": "active",
        "created_at": "2024-06-15T10:30:00Z",
        "company": "Streamline Analytics",
        "monthly_spend": 49.99,
    },
    "1234": {
        "customer_id": "1234",
        "name": "James Rodriguez",
        "email": "j.rodriguez@techcorp.io",
        "plan": "Enterprise",
        "status": "active",
        "created_at": "2023-11-02T14:20:00Z",
        "company": "TechCorp",
        "monthly_spend": 199.99,
    },
}


class LookupRequest(BaseModel):
    customer_id: str


def _normalize_id(raw: str) -> str:
    """Strip common prefixes: C-4821, #4821, 4821 all → 4821."""
    return raw.lstrip("#").removeprefix("C-").removeprefix("c-").strip()


@app.post("/api/customers/lookup")
async def customer_lookup(req: LookupRequest) -> dict[str, Any]:
    cid = _normalize_id(req.customer_id)
    customer = CUSTOMERS.get(cid)
    if customer:
        return customer
    return {"error": f"Customer {req.customer_id} not found", "found": False}


# --------------------------------------------------------------------------
# Billing History
# --------------------------------------------------------------------------

BILLING = {
    "4821": [
        {
            "date": "2026-03-01",
            "amount": 49.99,
            "description": "Pro Plan - Monthly",
            "status": "paid",
            "invoice_id": "INV-9923",
        },
        {
            "date": "2026-02-15",
            "amount": 49.99,
            "description": "Pro Plan Upgrade - Prorated",
            "status": "paid",
            "invoice_id": "INV-9887",
        },
        {
            "date": "2026-02-15",
            "amount": 49.99,
            "description": "Pro Plan Upgrade - Duplicate charge",
            "status": "paid",
            "invoice_id": "INV-9888",
        },
        {
            "date": "2026-02-01",
            "amount": 19.99,
            "description": "Starter Plan - Monthly",
            "status": "paid",
            "invoice_id": "INV-9845",
        },
        {
            "date": "2026-01-01",
            "amount": 19.99,
            "description": "Starter Plan - Monthly",
            "status": "paid",
            "invoice_id": "INV-9801",
        },
    ],
    "1234": [
        {
            "date": "2026-03-01",
            "amount": 199.99,
            "description": "Enterprise Plan - Monthly",
            "status": "paid",
            "invoice_id": "INV-9920",
        },
        {
            "date": "2026-02-01",
            "amount": 199.99,
            "description": "Enterprise Plan - Monthly",
            "status": "paid",
            "invoice_id": "INV-9856",
        },
    ],
}


class BillingRequest(BaseModel):
    customer_id: str
    months: int = 3


@app.post("/api/billing/history")
async def billing_history(req: BillingRequest) -> dict[str, Any]:
    cid = _normalize_id(req.customer_id)
    charges = BILLING.get(cid, [])
    return {
        "customer_id": cid,
        "charges": charges[: req.months * 5],
        "total_charges": len(charges),
    }


# --------------------------------------------------------------------------
# Create Ticket
# --------------------------------------------------------------------------


class TicketRequest(BaseModel):
    customer_id: str
    type: str
    subject: str
    description: str
    priority: str = "medium"
    amount: float | None = None


@app.post("/api/tickets/create")
async def create_ticket(req: TicketRequest) -> dict[str, Any]:
    ticket_id = f"TKT-{uuid.uuid4().hex[:8].upper()}"
    return {
        "ticket_id": ticket_id,
        "status": "open",
        "priority": req.priority,
        "assigned_to": "billing-team",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "estimated_resolution": "24-48 hours",
        "message": f"Ticket {ticket_id} created successfully. The billing team will process the ${req.amount:.2f} refund."
        if req.amount
        else f"Ticket {ticket_id} created successfully.",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
