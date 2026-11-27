"""Sales / transactions routes.

Critical requirement: every transaction has a client-generated tx_id (UUID).
Server uses INSERT ... ON CONFLICT (tx_id) DO NOTHING for idempotent offline sync.
"""

from fastapi import APIRouter, Query, status
from pydantic import BaseModel
from typing import Optional, List
import uuid

router = APIRouter()


class SaleItem(BaseModel):
    product_id: str
    quantity: int
    unit_price: float  # price at time of sale — snapshot, not live lookup


class SaleCreate(BaseModel):
    tx_id: str              # client-generated UUID — idempotency key
    branch_id: str
    items: List[SaleItem]
    payment_method: str     # "cash" | "transfer" | "pos" | "credit"
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None  # for walk-in without a customer record
    notes: Optional[str] = None
    recorded_at: Optional[str] = None   # ISO8601 — allows offline timestamp


@router.post("/", status_code=status.HTTP_201_CREATED)
async def record_sale(body: SaleCreate):
    """Record a sale. Idempotent — safe to retry after offline sync.

    Server behaviour:
    1. INSERT INTO transactions ... ON CONFLICT (tx_id) DO NOTHING
    2. For each item, deduct quantity from product stock
    3. If payment_method == 'credit', create a debt record
    4. Write audit log entry
    """
    # TODO: implement full DB logic
    total = sum(i.quantity * i.unit_price for i in body.items)
    return {
        "tx_id": body.tx_id,
        "status": "recorded",
        "total": total,
        "payment_method": body.payment_method,
        "items_count": len(body.items),
    }


@router.get("/")
async def list_sales(
    branch_id: str = Query(...),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    payment_method: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    """List sales with filters. Used by dashboard and reports."""
    return {"sales": [], "total": 0, "limit": limit, "offset": offset}


@router.get("/{tx_id}")
async def get_sale(tx_id: str):
    """Fetch a single transaction by its client tx_id or server id."""
    return {"tx_id": tx_id}


@router.delete("/{tx_id}", status_code=status.HTTP_200_OK)
async def void_sale(tx_id: str, reason: str = Query(...)):
    """Void a sale. Reverses stock deductions and marks transaction voided.
    Does NOT hard-delete — audit trail is preserved.
    """
    return {"tx_id": tx_id, "status": "voided", "reason": reason}
