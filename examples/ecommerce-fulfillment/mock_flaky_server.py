"""
Mock Flaky Server for the E-Commerce Fulfillment example.

Simulates an e-commerce fulfillment backend with intentional failures:
  - Inventory check: always succeeds
  - Payment processing: returns 503 on first attempt per order_id (RETRYABLE)
  - Shipping label creation: rejects requests missing weight_kg (RECOVERABLE)

Usage:
    uv run python examples/ecommerce-fulfillment/mock_flaky_server.py
"""

import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Response
from pydantic import BaseModel

app = FastAPI(title="Mock Flaky Server — Error Recovery Demo")

# Track payment attempts per order_id to return 503 on the first try.
_payment_attempts: dict[str, int] = {}


# --------------------------------------------------------------------------
# Inventory Check — always succeeds
# --------------------------------------------------------------------------

INVENTORY = {
    "SKU-2048": {
        "sku": "SKU-2048",
        "name": "Wireless Bluetooth Headphones",
        "in_stock": True,
        "quantity": 47,
        "weight_kg": 0.34,
        "warehouse": "US-WEST-2",
    },
    "SKU-1001": {
        "sku": "SKU-1001",
        "name": "USB-C Charging Cable (2m)",
        "in_stock": True,
        "quantity": 312,
        "weight_kg": 0.08,
        "warehouse": "US-EAST-1",
    },
}


class InventoryRequest(BaseModel):
    sku: str


@app.post("/api/inventory/check")
async def inventory_check(req: InventoryRequest):
    item = INVENTORY.get(req.sku)
    if item:
        return item
    return {"sku": req.sku, "in_stock": False, "error": f"SKU {req.sku} not found"}


# --------------------------------------------------------------------------
# Payment Processing — 503 on first attempt per order_id
# --------------------------------------------------------------------------


class PaymentRequest(BaseModel):
    order_id: str
    amount: float
    currency: str = "USD"


@app.post("/api/payments/process")
async def payment_process(req: PaymentRequest, response: Response):
    attempt = _payment_attempts.get(req.order_id, 0) + 1
    _payment_attempts[req.order_id] = attempt

    if attempt == 1:
        # First call: simulate transient gateway failure
        response.status_code = 503
        return {
            "error": "Payment gateway temporarily unavailable",
            "error_type": "server_error",
            "retry_after": 2,
        }

    # Subsequent calls: succeed
    payment_id = f"PAY-{uuid.uuid4().hex[:8].upper()}"
    return {
        "payment_id": payment_id,
        "order_id": req.order_id,
        "amount": req.amount,
        "currency": req.currency,
        "status": "completed",
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }


# --------------------------------------------------------------------------
# Shipping Label Creation — rejects missing weight_kg
# --------------------------------------------------------------------------


class ShippingRequest(BaseModel):
    order_id: str
    recipient_name: str
    address: str
    weight_kg: float | None = None


@app.post("/api/shipping/create")
async def shipping_create(req: ShippingRequest):
    if req.weight_kg is None:
        return {
            "error": "Missing required field: weight_kg. Every shipment must include the package weight in kilograms.",
            "error_type": "invalid_input",
            "required_fields": ["order_id", "recipient_name", "address", "weight_kg"],
        }

    shipment_id = f"SHP-{uuid.uuid4().hex[:8].upper()}"
    tracking = f"1Z{uuid.uuid4().hex[:12].upper()}"
    return {
        "shipment_id": shipment_id,
        "tracking_number": tracking,
        "carrier": "UPS",
        "estimated_delivery": "2-3 business days",
        "weight_kg": req.weight_kg,
        "status": "label_created",
    }


# --------------------------------------------------------------------------
# Health & reset
# --------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mock-flaky-server"}


@app.post("/reset")
async def reset():
    """Reset failure counters — useful for re-running the demo."""
    _payment_attempts.clear()
    return {"status": "reset", "message": "All failure counters cleared"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
