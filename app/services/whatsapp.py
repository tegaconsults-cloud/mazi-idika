"""WhatsApp Cloud API service — send messages and handle routing."""

import httpx
from app.core.config import settings

WHATSAPP_API_URL = "https://graph.facebook.com/v19.0"


async def send_text_message(to: str, body: str) -> dict:
    """Send a plain text WhatsApp message."""
    url = f"{WHATSAPP_API_URL}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def send_template_message(to: str, template_name: str, components: list) -> dict:
    """Send a WhatsApp template message (e.g. debt reminder)."""
    url = f"{WHATSAPP_API_URL}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "en"},
            "components": components,
        },
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


def parse_incoming_message(payload: dict) -> dict | None:
    """Extract message data from a WhatsApp webhook payload."""
    try:
        entry = payload["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        messages = value.get("messages", [])
        if not messages:
            return None
        msg = messages[0]
        return {
            "from": msg["from"],
            "type": msg["type"],                        # "text" | "audio"
            "text": msg.get("text", {}).get("body"),
            "audio_id": msg.get("audio", {}).get("id"),
            "timestamp": msg.get("timestamp"),
        }
    except (KeyError, IndexError):
        return None
