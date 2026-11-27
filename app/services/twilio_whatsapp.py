"""Twilio WhatsApp service.

Responsibilities:
- Validate Twilio request signatures (security)
- Parse incoming Twilio webhook payloads (text, audio, image, document)
- Send replies back via the Twilio WhatsApp API
- Download media attachments for voice/image processing

Security contract
-----------------
validate_twilio_signature() has three distinct states:

1. TWILIO_AUTH_TOKEN not configured AND APP_ENV == "development"
   → returns True (dev-only bypass, explicit environment gate)

2. TWILIO_AUTH_TOKEN not configured AND APP_ENV != "development"
   → returns False (production must have credentials)

3. TWILIO_AUTH_TOKEN configured
   → delegates to Twilio's RequestValidator (HMAC-SHA1)
     returns True only if signature matches

Callers must treat False as a hard rejection, not a soft warning.
"""

import logging
from typing import Optional

import httpx
from twilio.rest import Client as TwilioClient
from twilio.request_validator import RequestValidator

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Twilio client ─────────────────────────────────────────────────────────────

def get_twilio_client() -> Optional[TwilioClient]:
    """Return a Twilio REST client, or None if credentials are missing."""
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        logger.warning("Twilio credentials not configured — client unavailable.")
        return None
    return TwilioClient(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


# ── Signature validation ──────────────────────────────────────────────────────

def validate_twilio_signature(
    url: str,
    post_params: dict,
    signature: str,
) -> bool:
    """
    Validate that a request genuinely came from Twilio.

    Returns True  → request is authenticated
    Returns False → reject the request

    See: https://www.twilio.com/docs/usage/security#validating-signatures-from-twilio
    """
    if not settings.TWILIO_AUTH_TOKEN:
        if settings.APP_ENV != "development":
            # Production server with missing credentials — deny everything.
            # This prevents an accidentally unconfigured server from accepting
            # arbitrary requests as valid.
            logger.error(
                "TWILIO_AUTH_TOKEN is not set in a non-development environment. "
                "Rejecting all Twilio webhook requests."
            )
            return False

        # Development-only bypass — log clearly so it is never missed.
        logger.warning(
            "TWILIO_AUTH_TOKEN not set — skipping signature validation "
            "(APP_ENV=development only). This MUST NOT happen in production."
        )
        return True

    # Credentials present — always validate.
    if not signature:
        logger.warning("Twilio request has no X-Twilio-Signature header — rejecting.")
        return False

    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
    valid = validator.validate(url, post_params, signature)
    if not valid:
        logger.warning(f"Twilio HMAC validation failed for URL: {url}")
    return valid


# ── Payload parsing ───────────────────────────────────────────────────────────

class TwilioMessage:
    """Normalised representation of an incoming Twilio WhatsApp message."""

    def __init__(self, form: dict):
        self.message_sid: str               = form.get("MessageSid", "")
        self.from_number: str               = form.get("From", "").replace("whatsapp:", "")
        self.to_number: str                 = form.get("To", "").replace("whatsapp:", "")
        self.body: str                      = form.get("Body", "").strip()
        self.num_media: int                 = int(form.get("NumMedia", 0))
        self.profile_name: str              = form.get("ProfileName", "")
        self.media_url: Optional[str]       = form.get("MediaUrl0")
        self.media_content_type: Optional[str] = form.get("MediaContentType0")

        if self.num_media > 0 and self.media_content_type:
            ct = self.media_content_type.lower()
            if ct.startswith("audio"):
                self.msg_type = "audio"
            elif ct.startswith("image"):
                self.msg_type = "image"
            elif ct == "application/pdf" or "document" in ct:
                self.msg_type = "document"
            else:
                self.msg_type = "media"
        else:
            self.msg_type = "text"

    def __repr__(self) -> str:
        return (
            f"<TwilioMessage from={self.from_number!r} "
            f"type={self.msg_type!r} body={self.body[:40]!r}>"
        )


def parse_twilio_webhook(form: dict) -> TwilioMessage:
    """Parse a Twilio webhook POST form-data dict into a TwilioMessage."""
    return TwilioMessage(form)


# ── Send helpers ──────────────────────────────────────────────────────────────

def _whatsapp_number(number: str) -> str:
    if not number.startswith("whatsapp:"):
        return f"whatsapp:{number}"
    return number


def send_text_reply(to: str, body: str) -> bool:
    """
    Send a plain-text WhatsApp message via Twilio.

    Returns True on success, False on failure.
    Never raises — the agent must continue regardless.
    Truncates body to 1600 chars (Twilio hard limit).
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
        logger.exception(f"Twilio send_text_reply failed to {to}: {exc}")
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
        logger.exception(f"Twilio send_media_reply failed to {to}: {exc}")
        return False


# ── Media download ────────────────────────────────────────────────────────────

async def download_media(media_url: str) -> Optional[bytes]:
    """
    Download a media file from Twilio.
    Twilio media URLs require HTTP Basic Auth (SID + auth token).
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
