"""Twilio WhatsApp webhook endpoint.

Security layers:
1. Twilio request signature validation (X-Twilio-Signature header)
2. Optional shared-secret token in URL query param (?token=...)
3. All DB operations are scoped to the authenticated business (by phone)

Twilio sends multipart/form-data POST requests to this endpoint.
We MUST reply with HTTP 200 quickly (< 5s) or Twilio will retry.
Heavy work (LLM, DB) is awaited but the timeout is handled gracefully.
"""

import logging
import asyncio
from typing import Annotated

from fastapi import APIRouter, Request, HTTPException, Query, status, Form
from fastapi.responses import Response

from app.core.config import settings
from app.services.twilio_whatsapp import (
    validate_twilio_signature,
    parse_twilio_webhook,
    send_text_reply,
)
from app.services.agent import handle_message

logger = logging.getLogger(__name__)

router = APIRouter()

# Twilio expects a TwiML or plain 200 response — we return 200 with no body
# and send the reply as a separate outbound message via the REST API.
# This avoids the 5-second reply window constraint for complex operations.

AGENT_TIMEOUT_SECONDS = 25  # Render Free can be slow; stay under 30s


@router.post(
    "/twilio/whatsapp",
    status_code=status.HTTP_200_OK,
    summary="Twilio WhatsApp incoming message webhook",
    tags=["Webhooks"],
)
async def twilio_whatsapp_incoming(
    request: Request,
    token: str = Query(default="", description="Shared secret for extra security"),
):
    """
    Receives incoming WhatsApp messages forwarded by Twilio.

    Security:
    - Validates X-Twilio-Signature against TWILIO_AUTH_TOKEN
    - Optionally validates ?token= query param against TWILIO_WEBHOOK_SECRET

    After validation, hands off to the agent and replies via outbound API.
    Always returns 200 so Twilio doesn't retry.
    """

    # ── 1. Optional shared-secret token check ────────────────────────────
    if settings.TWILIO_WEBHOOK_SECRET and token != settings.TWILIO_WEBHOOK_SECRET:
        logger.warning("Twilio webhook: invalid shared-secret token.")
        # Return 200 to avoid Twilio retries leaking info, but do nothing
        return Response(status_code=200)

    # ── 2. Parse form data ────────────────────────────────────────────────
    try:
        form = dict(await request.form())
    except Exception as exc:
        logger.error(f"Failed to parse Twilio form data: {exc}")
        return Response(status_code=200)

    # ── 3. Twilio signature validation ────────────────────────────────────
    signature = request.headers.get("X-Twilio-Signature", "")

    # Reconstruct the full URL as Twilio signed it
    # On Render, the host header reflects the public URL
    proto  = request.headers.get("X-Forwarded-Proto", "https")
    host   = request.headers.get("X-Forwarded-Host") or request.headers.get("host", "")
    path   = request.url.path
    # Include the token query param in the URL if present (Twilio signs the full URL)
    query  = f"?token={token}" if token else ""
    full_url = f"{proto}://{host}{path}{query}"

    if not validate_twilio_signature(full_url, dict(form), signature):
        logger.warning(f"Twilio signature validation failed for {full_url}")
        # Still return 200 — don't expose validation failures via status codes
        return Response(status_code=200)

    # ── 4. Parse the message ──────────────────────────────────────────────
    try:
        msg = parse_twilio_webhook(form)
    except Exception as exc:
        logger.error(f"Failed to parse Twilio message: {exc}")
        return Response(status_code=200)

    logger.info(f"Twilio WA in: from={msg.from_number} type={msg.msg_type} body={msg.body[:60]!r}")

    # ── 5. Run agent with timeout ─────────────────────────────────────────
    try:
        reply = await asyncio.wait_for(
            handle_message(msg),
            timeout=AGENT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.error(f"Agent timed out for {msg.from_number}")
        reply = (
            "⏳ That took too long to process. Please try again.\n"
            "If the problem persists, try a simpler message."
        )
    except Exception as exc:
        logger.exception(f"Agent error for {msg.from_number}: {exc}")
        reply = "⚠️ Something went wrong. Please try again in a moment."

    # ── 6. Send reply ─────────────────────────────────────────────────────
    if reply:
        sent = send_text_reply(msg.from_number, reply)
        if not sent:
            logger.error(f"Failed to send reply to {msg.from_number}")

    # Always 200 — Twilio must not retry
    return Response(status_code=200)


@router.get(
    "/twilio/whatsapp",
    summary="Twilio webhook health check",
    tags=["Webhooks"],
)
async def twilio_whatsapp_health():
    """Simple GET endpoint to verify Twilio webhook URL is reachable."""
    configured = bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN)
    return {
        "status": "ok",
        "twilio_configured": configured,
        "endpoint": "/api/v1/webhooks/twilio/whatsapp",
    }
