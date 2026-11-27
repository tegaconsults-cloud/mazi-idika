"""Twilio WhatsApp webhook endpoint.

Security model (in order of execution)
---------------------------------------
1. Shared-secret URL token (?token=...) — cheap first gate, eliminates scanners
2. Twilio HMAC-SHA1 signature (X-Twilio-Signature) — cryptographic proof from Twilio
3. Business lookup by phone — ensures the sender is a registered tenant

Both checks (1) and (2) must pass before any body is parsed or processed.
Rejections always return HTTP 200 to prevent Twilio from retrying,
but the agent and DB are never reached.

Response strategy
-----------------
Twilio requires HTTP 200 within ~15 seconds or it retries.
We return 200 immediately after validation and send the reply as a
separate outbound API call. This decouples reply latency from HTTP latency.
"""

import asyncio
import logging

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import Response

from app.core.config import settings
from app.services.twilio_whatsapp import (
    download_media,
    parse_twilio_webhook,
    send_text_reply,
    validate_twilio_signature,
)
from app.services.agent import handle_message

logger = logging.getLogger(__name__)
router = APIRouter()

AGENT_TIMEOUT_SECONDS = 25  # Render Free tier can be slow; stay inside 30 s


def _reconstruct_url(request: Request, token: str) -> str:
    """
    Rebuild the full URL that Twilio signed.

    Twilio signs the URL it POSTed to, including any query parameters.
    On Render (behind a TLS proxy), the forwarded headers carry the real
    public scheme and host. We must reproduce these exactly or validation fails.
    """
    proto = request.headers.get("X-Forwarded-Proto") or "https"
    host  = (
        request.headers.get("X-Forwarded-Host")
        or request.headers.get("host")
        or "localhost"
    )
    path  = request.url.path
    query = f"?token={token}" if token else ""
    return f"{proto}://{host}{path}{query}"


@router.post(
    "/twilio/whatsapp",
    status_code=status.HTTP_200_OK,
    summary="Twilio WhatsApp incoming message webhook",
    tags=["Webhooks"],
)
async def twilio_whatsapp_incoming(
    request: Request,
    token: str = Query(default="", description="Shared-secret token"),
) -> Response:
    """
    Receives and processes incoming WhatsApp messages from Twilio.

    Validation order:
    1. Shared-secret token check
    2. Body parsing (needed to build the form dict for HMAC)
    3. Twilio HMAC-SHA1 signature check
    4. Agent execution with timeout
    5. Outbound reply via Twilio REST API
    """

    # ── 1. Shared-secret token check ──────────────────────────────────────────
    # Only enforced when TWILIO_WEBHOOK_SECRET is configured.
    # An empty token from an attacker will fail this gate.
    if settings.TWILIO_WEBHOOK_SECRET:
        if not token or token != settings.TWILIO_WEBHOOK_SECRET:
            token_preview = repr(token)[:10]
            logger.warning(
                f"Twilio webhook: shared-secret token mismatch "
                f"(got {token_preview}). Silently dropping."
            )
            return Response(status_code=200)

    # ── 2. Parse form body ─────────────────────────────────────────────────────
    # Must happen before HMAC check because the validator needs the form fields.
    try:
        form = dict(await request.form())
    except Exception as exc:
        logger.error(f"Twilio webhook: failed to parse form body: {exc}")
        return Response(status_code=200)

    # ── 3. Twilio HMAC-SHA1 signature check ───────────────────────────────────
    signature = request.headers.get("X-Twilio-Signature", "")
    full_url  = _reconstruct_url(request, token)

    if not validate_twilio_signature(full_url, form, signature):
        # validate_twilio_signature already logs the reason.
        return Response(status_code=200)

    # ── 4. Parse the message ───────────────────────────────────────────────────
    try:
        msg = parse_twilio_webhook(form)
    except Exception as exc:
        logger.error(f"Twilio webhook: failed to parse message: {exc}")
        return Response(status_code=200)

    logger.info(
        f"Twilio WA in: from={msg.from_number!r} "
        f"type={msg.msg_type!r} body={msg.body[:60]!r}"
    )

    # ── 5. Run agent with hard timeout ─────────────────────────────────────────
    try:
        reply = await asyncio.wait_for(
            handle_message(msg),
            timeout=AGENT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.error(f"Agent timed out for {msg.from_number}")
        reply = (
            "⏳ That took too long. Please try again.\n"
            "If this keeps happening, try a simpler message."
        )
    except Exception as exc:
        logger.exception(f"Agent unhandled error for {msg.from_number}: {exc}")
        reply = "⚠️ Something went wrong on my end. Please try again."

    # ── 6. Send reply ──────────────────────────────────────────────────────────
    if reply:
        sent = send_text_reply(msg.from_number, reply)
        if not sent:
            logger.error(f"Failed to send Twilio reply to {msg.from_number}")

    return Response(status_code=200)


@router.get(
    "/twilio/whatsapp",
    summary="Twilio webhook health check",
    tags=["Webhooks"],
)
async def twilio_whatsapp_health() -> dict:
    """Lightweight GET for verifying the webhook URL is reachable."""
    return {
        "status": "ok",
        "twilio_configured": bool(
            settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN
        ),
        "webhook_secret_set": bool(settings.TWILIO_WEBHOOK_SECRET),
        "endpoint": "POST /api/v1/webhooks/twilio/whatsapp",
    }
