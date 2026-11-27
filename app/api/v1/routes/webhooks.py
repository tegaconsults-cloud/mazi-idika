"""Webhook receivers — WhatsApp Cloud API and Paystack."""

from fastapi import APIRouter, Request, Query, HTTPException, status
from app.core.config import settings

router = APIRouter()


# ── WhatsApp Cloud API ──────────────────────────────────────────────────────

@router.get("/whatsapp")
async def whatsapp_verify(
    request: Request,
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    """Meta webhook verification handshake."""
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        return int(hub_challenge)
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification failed.")


@router.post("/whatsapp", status_code=status.HTTP_200_OK)
async def whatsapp_incoming(request: Request):
    """Receive incoming WhatsApp messages.

    Flow:
    1. Parse message type (text | audio)
    2. If audio → transcribe via speech-to-text API
    3. Pass text to AI intent classifier
    4. Route to appropriate service (record_sale, adjust_stock, query_debts, etc.)
    5. Reply via WhatsApp Cloud API send message endpoint
    """
    payload = await request.json()
    # TODO: implement full message routing pipeline (Phase 4)
    return {"status": "received"}


# ── Paystack ────────────────────────────────────────────────────────────────

@router.post("/paystack", status_code=status.HTTP_200_OK)
async def paystack_webhook(request: Request):
    """Receive Paystack payment events.

    Handles:
    - charge.success  → activate or extend subscription
    - subscription.disable → downgrade plan
    - invoice.payment_failed → notify user
    """
    # TODO: verify Paystack HMAC signature using PAYSTACK_SECRET_KEY
    # TODO: handle charge.success → update business.plan in DB
    payload = await request.json()
    event = payload.get("event")
    return {"status": "received", "event": event}
