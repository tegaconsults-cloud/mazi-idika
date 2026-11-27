# Twilio WhatsApp Integration — Setup Guide

## Webhook URL

After deploying to Render, your Twilio webhook URL is:

```
https://<your-render-service>.onrender.com/api/v1/webhooks/twilio/whatsapp?token=<TWILIO_WEBHOOK_SECRET>
```

Configure this in **Twilio Console → Messaging → Sandbox (or Sender) → When a message comes in**.

---

## 1. Get Twilio credentials

1. Sign up / log in at https://console.twilio.com
2. Note your **Account SID** and **Auth Token** from the dashboard
3. Go to **Messaging → Try it out → Send a WhatsApp message**
4. Note the sandbox number (e.g. `+14155238886`)
5. Follow the sandbox join instructions (send "join <word>" to the sandbox number)

---

## 2. Environment variables (Render dashboard)

Set these in **Render → Your Service → Environment**:

| Variable | Value |
|---|---|
| `TWILIO_ACCOUNT_SID` | From Twilio console |
| `TWILIO_AUTH_TOKEN` | From Twilio console |
| `TWILIO_WHATSAPP_NUMBER` | e.g. `+14155238886` |
| `TWILIO_WEBHOOK_SECRET` | Any random string you invent |
| `GEMINI_API_KEY` | From https://aistudio.google.com/app/apikey |
| `OPENAI_API_KEY` | From https://platform.openai.com (optional) |
| `DEEPGRAM_API_KEY` | From https://console.deepgram.com (optional) |
| `DATABASE_URL` | Supabase PostgreSQL URL |

---

## 3. Configure the Twilio sandbox webhook

1. Twilio Console → **Messaging → Try it out → Send a WhatsApp message**
2. Under **Sandbox Configuration**, set:
   - **When a message comes in**: `https://<render-url>/api/v1/webhooks/twilio/whatsapp?token=<TWILIO_WEBHOOK_SECRET>`
   - Method: **HTTP POST**
3. Click Save

---

## 4. Test the integration

```bash
# 1. Check the webhook is reachable
curl https://<render-url>/api/v1/webhooks/twilio/whatsapp

# 2. Run the test suite locally
pip install -r requirements.txt
pytest tests/ -v

# 3. Send a WhatsApp message to the Twilio sandbox number
# Try: "today summary" or "who dey owe me?"
```

---

## 5. Database setup

The agent reads from and writes to your Supabase PostgreSQL database.
You need to run Alembic migrations to create the tables:

```bash
# Set DATABASE_URL in your .env first
alembic upgrade head
```

If you don't have an Alembic migration yet, create one:
```bash
alembic init alembic
# Edit alembic/env.py to import your models
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

---

## 6. What works right now

| Feature | Status |
|---|---|
| Twilio signature validation | ✅ Implemented |
| Shared-secret URL token | ✅ Implemented |
| Text message routing | ✅ Implemented |
| Voice note transcription | ✅ Deepgram + Whisper fallback |
| Product image OCR | ✅ Gemini Vision + GPT-4o fallback |
| PDF document extraction | ✅ pdfplumber (install separately) |
| Record a sale | ✅ Writes to DB, deducts stock |
| Restock / add product | ✅ Updates or creates product |
| Query stock levels | ✅ Reads from DB |
| Record a debt | ✅ Creates debt record |
| Settle a debt | ✅ Updates debt status |
| Query outstanding debts | ✅ Reads from DB |
| Daily / weekly / monthly summary | ✅ Aggregates from transactions |
| Top selling products | ✅ Aggregates from transaction items |
| Expense recording | ⚠️ Acknowledged but not persisted (Expense model needed in Phase 2) |
| Automatic debt reminders | ⚠️ Queued but not sent (needs customer phones stored) |
| Multi-language (Pidgin/Yoruba/Hausa/Igbo) | ✅ via Gemini LLM prompt |
| Keyword fallback (no LLM) | ✅ Always works |
| Business isolation (multi-tenant) | ✅ All DB ops scoped to business.id |
| Meta WhatsApp integration | ✅ Unchanged — still at /api/v1/webhooks/whatsapp |

## 7. What still needs work (honest assessment)

| Gap | What's needed |
|---|---|
| Alembic migrations | No migration files exist yet — tables must be created before the agent can read/write |
| Supabase auth integration | `security.py` has JWT helpers but no middleware enforcing auth on routes |
| Expense model | No `expenses` table — `add_expense` intent is acknowledged but not persisted |
| Paystack webhook | Stubbed — no plan activation logic |
| Customer phone lookup for reminders | Debt reminders need phone numbers stored at debt-creation time |
| Supplier management | No supplier model or routes yet |
| Multi-branch selection via chat | Agent defaults to first branch — user can't select branch via WhatsApp yet |
| Rate limiting | No per-number rate limiting (easy to add with Redis or in-memory dict) |
