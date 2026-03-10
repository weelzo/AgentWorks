"""
Mock Supplier Server for the Inventory Restock example.

Simulates an inventory management backend where the supplier API
has expired credentials — triggering a FATAL (Tier 3) error.

Endpoints:
  - inventory_levels: always succeeds (low stock detected)
  - supplier_order: always returns HTTP 401 (expired API key)
  - restock_notify: always succeeds (but agent never reaches it)

Usage:
    uv run python examples/inventory-restock/mock_supplier_server.py
"""

import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Response
from pydantic import BaseModel

app = FastAPI(title="Mock Supplier Server — Fatal Error Demo")


# --------------------------------------------------------------------------
# Inventory Levels — always succeeds, shows critically low stock
# --------------------------------------------------------------------------

INVENTORY = {
    "SKU-3001": {
        "sku": "SKU-3001",
        "name": "Industrial Pressure Sensor",
        "quantity": 3,
        "reorder_threshold": 25,
        "unit_cost_usd": 84.50,
        "supplier": "TechParts Global",
        "warehouse": "US-EAST-1",
        "status": "critical_low",
    },
    "SKU-3002": {
        "sku": "SKU-3002",
        "name": "Temperature Probe Module",
        "quantity": 142,
        "reorder_threshold": 50,
        "unit_cost_usd": 32.00,
        "supplier": "TechParts Global",
        "warehouse": "US-EAST-1",
        "status": "in_stock",
    },
}


class LevelsRequest(BaseModel):
    sku: str


@app.post("/api/inventory/levels")
async def inventory_levels(req: LevelsRequest):
    item = INVENTORY.get(req.sku)
    if item:
        return item
    return {"sku": req.sku, "error": f"SKU {req.sku} not found in warehouse"}


# --------------------------------------------------------------------------
# Supplier Order — always returns 401 (expired credentials)
# --------------------------------------------------------------------------


class OrderRequest(BaseModel):
    sku: str
    quantity: int
    priority: str = "standard"


@app.post("/api/supplier/order")
async def supplier_order(req: OrderRequest, response: Response):
    # Simulate expired API key — this triggers FATAL in the ErrorClassifier
    response.status_code = 401
    return {
        "error": "Authentication failed: API key has expired. Contact supplier to renew credentials.",
        "error_code": "EXPIRED_API_KEY",
        "supplier": "TechParts Global",
        "support_email": "api-support@techpartsglobal.example.com",
    }


# --------------------------------------------------------------------------
# Restock Notification — succeeds but agent never reaches it
# --------------------------------------------------------------------------


class NotifyRequest(BaseModel):
    sku: str
    order_id: str
    estimated_arrival: str


@app.post("/api/warehouse/notify")
async def restock_notify(req: NotifyRequest):
    notification_id = f"NTF-{uuid.uuid4().hex[:8].upper()}"
    return {
        "notification_id": notification_id,
        "sku": req.sku,
        "warehouse": "US-EAST-1",
        "message": f"Warehouse team notified about incoming restock for {req.sku}",
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mock-supplier-server"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8003)
