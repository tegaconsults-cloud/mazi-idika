"""Twilio WhatsApp service.

Responsibilities:
- Validate Twilio request signatures (security)
- Parse incoming Twilio webhook payloads (text, audio, image, document)
- Send replies back via the Twilio WhatsApp API
- Download media attachments for voice/image processing
"""

import hashlib
import hmac
import base64
import logging
from urllib.parse import urlencode, quote
from typing import Optional

import httpx
from twilio.rest import Client as TwilioClient
from twilio.request_validator import RequestValidator

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Twilio client (lazy — only initialised when credentials are present) ──────

def get_twilio_client() -> Optional[TwilioClient]:
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        logger.warning("Twilio credentials not configured — client unavailable.")
        return None
    return TwilioClient(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def get_request_validator() -> Optional[RequestValidator]:
    if not settings.TWILIO_AUTH_TOKEN:
        return None
    return RequestValidator(settings.TWILIO_AUTH_TOKEN)


# ── Signature validation ──────────────────────────────────────────────────────

def validate_twilio_signature(
    url: str,
    post_params: dict,
    signature: str,
) -> bool:
    """
    Validate that the request genuinely came from Twilio.
    https://www.twilio.com/docs/usage/security#validating-signatures-from-twilio

    Returns True if valid, False otherwise.
    If TWILIO_AUTH_TOKEN is not configured, skips validation and logs a warning
    (safe for local development; never skip in production).
    """
    if not settings.TWILIO_AUTH_TOKEN:
        logger.warning("TWILIO_AUTH_TOKEN not set — skipping signature validation (dev mode).")
        return True

    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
    return validator.validate(url, post_params, signature)


# ── Payload parsing ───────────────────────────────────────────────────────────

class TwilioMessage:
    """Normalised representation of an incoming Twilio WhatsApp message."""

    def __init__(self, form: dict):
        self.message_sid: str        = form.get("MessageSid", "")
        self.from_number: str        = form.get("From", "").replace("whatsapp:", "")
        self.to_number: str          = form.get("To", "").replace("whatsapp:", "")
        self.body: str               = form.get("Body", "").strip()
        self.num_media: int          = int(form.get("NumMedia", 0))
        self.profile_name: str       = form.get("ProfileName", "")

        # Media (voice note, image, document)
        self.media_url: Optional[str]          = form.get("MediaUrl0")
        self.media_content_type: Optional[str] = form.get("MediaContentType0")

        # Derived message type
        if self.num_media > 0 and self.media_content_type:
            ct = self.media_content_type.lower()
            if ct.startswith("audio"):
                self.msg_type = "audio"
            elif ct.startswith("image"):
                self.msg_type = "image"
            elif ct in ("application/pdf",) or "document" in ct:
                self.msg_type = "document"
            else:
                self.msg_type = "media"
        else:
            self.msg_type = "text"

    def __repr__(self):
        return f"<TwilioMessage from={self.from_number} type={self.msg_type} body={self.body[:40]!r}>"


def parse_twilio_webhook(form: dict) -> TwilioMessage:
    """Parse a Twilio webhook POST form-data dict into a TwilioMessage."""
    return TwilioMessage(form)


# ── Send helpers ──────────────────────────────────────────────────────────────

def _whatsapp_number(number: str) -> str:
    """Ensure the number has the whatsapp: prefix."""
    if not number.startswith("whatsapp:"):
        return f"whatsapp:{number}"
    return number


def send_text_reply(to: str, body: str) -> bool:
    """
    Send a plain-text WhatsApp message via Twilio.
    Returns True on success, False on failure (never raises — agent must keep running).
    Truncates body to 1600 chars (Twilio limit).
    """
    client = get_twilio_client()
    if not client:
        logger.error("Cannot send reply — Twilio client not initialised.")
        return False

    if not settings.TWILIO_WHATSAPP_NUMBER:
        logger.error("TWILIO_WHATSAPP_NUMBER not configured.")
        return False

    try:
        client.messages.create(
            from_=_whatsapp_number(settings.TWILIO_WHATSAPP_NUMBER),
            to=_whatsapp_number(to),
            body=body[:1600],
        )
        return True
    except Exception as exc:
        logger.exception(f"Twilio send failed to {to}: {exc}")
        return False


def send_media_reply(to: str, body: str, media_url: str) -> bool:
    """Send a WhatsApp message with a media attachment."""
    client = get_twilio_client()
    if not client or not settings.TWILIO_WHATSAPP_NUMBER:
        return False
    try:
        client.messages.create(
            from_=_whatsapp_number(settings.TWILIO_WHATSAPP_NUMBER),
            to=_whatsapp_number(to),
            body=body[:1600],
            media_url=[media_url],
        )
        return True
    except Exception as exc:
        logger.exception(f"Twilio media send failed to {to}: {exc}")
        return False


# ── Media download ────────────────────────────────────────────────────────────

async def download_media(media_url: str) -> Optional[bytes]:
    """
    Download a media file from Twilio.
    Twilio media URLs require HTTP Basic Auth with account SID and auth token.
    Returns raw bytes, or None on failure.
    """
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        logger.error("Twilio credentials not set — cannot download media.")
        return None

    auth = (settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(media_url, auth=auth)
            resp.raise_for_status()
            return resp.content
    except Exception as exc:
        logger.exception(f"Failed to download Twilio media from {media_url}: {exc}")
        return None
