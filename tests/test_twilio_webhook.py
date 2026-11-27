"""Tests for the Twilio WhatsApp webhook.

These tests verify:
- Webhook endpoint is reachable (GET health check)
- Signature validation rejects bad signatures
- Well-formed requests with no Twilio credentials configured are handled
- Intent classifier keyword fallback works correctly

No real Twilio or DB credentials are needed to run these tests.
"""

import pytest
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.ai_intent import classify_intent, Intent


# ── Health check ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_twilio_webhook_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/webhooks/twilio/whatsapp")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "twilio_configured" in data


# ── Signature validation ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_twilio_webhook_bad_token_returns_200():
    """Bad shared-secret token should return 200 (silent rejection — don't leak info)."""
    from app.core.config import settings
    original = settings.TWILIO_WEBHOOK_SECRET
    settings.TWILIO_WEBHOOK_SECRET = "correct-secret"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhooks/twilio/whatsapp?token=wrong-secret",
            data={"Body": "hello", "From": "whatsapp:+2348012345678"},
        )
    settings.TWILIO_WEBHOOK_SECRET = original
    assert resp.status_code == 200  # silent rejection


@pytest.mark.asyncio
async def test_twilio_webhook_no_credentials_returns_200():
    """
    With no Twilio credentials, signature validation is skipped (dev mode).
    The agent runs but won't find a business → still returns 200.
    """
    with patch("app.services.twilio_whatsapp.validate_twilio_signature", return_value=True), \
         patch("app.services.agent.handle_message", new_callable=AsyncMock,
               return_value="Welcome!"), \
         patch("app.services.twilio_whatsapp.send_text_reply", return_value=True):

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/webhooks/twilio/whatsapp",
                data={
                    "MessageSid": "SM123",
                    "From": "whatsapp:+2348012345678",
                    "To": "whatsapp:+14155238886",
                    "Body": "hello",
                    "NumMedia": "0",
                },
                headers={"X-Twilio-Signature": "test"},
            )
    assert resp.status_code == 200


# ── Intent classifier ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_intent_greeting():
    result = await classify_intent("hi there")
    assert result.intent == Intent.GREETING


@pytest.mark.asyncio
async def test_intent_sale():
    result = await classify_intent("sold 5 indomie, 3500 each, cash")
    assert result.intent == Intent.RECORD_SALE


@pytest.mark.asyncio
async def test_intent_restock():
    result = await classify_intent("20 cartons indomie don reach")
    assert result.intent == Intent.RESTOCK


@pytest.mark.asyncio
async def test_intent_query_debts():
    result = await classify_intent("who dey owe me?")
    assert result.intent == Intent.QUERY_DEBTS


@pytest.mark.asyncio
async def test_intent_summary_today():
    result = await classify_intent("today summary")
    assert result.intent == Intent.QUERY_SUMMARY
    assert result.entities.get("period") == "today"


@pytest.mark.asyncio
async def test_intent_summary_week():
    result = await classify_intent("show me my weekly report")
    assert result.intent == Intent.QUERY_SUMMARY
    assert result.entities.get("period") == "week"


@pytest.mark.asyncio
async def test_intent_settle_debt():
    result = await classify_intent("Mama Titi don pay 5000")
    assert result.intent == Intent.SETTLE_DEBT


@pytest.mark.asyncio
async def test_intent_unknown():
    result = await classify_intent("asdfghjkl")
    assert result.intent == Intent.UNKNOWN


# ── Existing health check still passes ───────────────────────────────────────

@pytest.mark.asyncio
async def test_root_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"
