"""Debts & credit routes."""

from fastapi import APIRouter, Query, status
from pydantic import BaseModel
from typing import Optional
import uuid

router = APIRouter()


class DebtCreate(BaseModel):
    branch_id: str
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None  # walk-in customer
    customer_phone: Optional[str] = None
    amount: float
    description: Optional[str] = None
    sale_tx_id: Optional[str] = None     # link to originating sale


class DebtSettle(BaseModel):
    amount_paid: float
    payment_method: str  # "cash" | "transfer" | "pos"
    notes: Optional[str] = None


class ReminderSend(BaseModel):
    debt_ids: list[str]  # list of debt IDs to remind
    channel: str = "whatsapp"  # "whatsapp" | "sms"


@router.get("/")
async def list_debts(
    branch_id: str = Query(...),
    overdue_only: bool = Query(False),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    """List outstanding debts for a branch."""
    return {"debts": [], "total_outstanding": 0, "limit": limit, "offset": offset}


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_debt(body: DebtCreate):
    """Create a debt record manually (not from a credit sale)."""
    return {"id": str(uuid.uuid4()), **body.model_dump()}


@router.post("/{debt_id}/settle")
async def settle_debt(debt_id: str, body: DebtSettle):
    """Record a full or partial payment against a debt."""
    return {
        "debt_id": debt_id,
        "amount_paid": body.amount_paid,
        "payment_method": body.payment_method,
        "status": "settled" if body.amount_paid else "partial",
    }


@router.post("/remind", status_code=status.HTTP_202_ACCEPTED)
async def send_reminders(body: ReminderSend):
    """Send WhatsApp or SMS reminders to debtors."""
    # TODO: integrate WhatsApp Cloud API or Termii SMS
    return {
        "queued": len(body.debt_ids),
        "channel": body.channel,
        "status": "queued",
    }


@router.get("/{debt_id}")
async def get_debt(debt_id: str):
    return {"id": debt_id}
