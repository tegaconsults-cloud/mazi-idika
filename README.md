# Mazi Idika — Backend API

> *Idika* = market in Igbo.  
> FastAPI backend for the Mazi Nigerian business management platform.

## Stack
- **Framework:** Python FastAPI (async)
- **Database:** PostgreSQL via Supabase (Row-Level Security enforced)
- **Auth:** Supabase Auth (phone OTP)
- **Payments:** Paystack
- **WhatsApp:** Meta WhatsApp Cloud API
- **AI:** LLM API + intent classifier (Phase 4)
- **Voice:** Deepgram / Whisper (Phase 4)

## Project structure

```
app/
├── main.py               ← FastAPI app, CORS, router mount
├── core/
│   ├── config.py         ← Settings from environment variables
│   └── security.py       ← JWT helpers
├── api/
│   └── v1/
│       ├── router.py     ← Central router
│       └── routes/
│           ├── auth.py
│           ├── businesses.py
│           ├── products.py
│           ├── sales.py
│           ├── debts.py
│           ├── customers.py
│           ├── reports.py
│           └── webhooks.py
├── models/               ← SQLAlchemy ORM models
├── schemas/              ← Pydantic request/response schemas (Phase 1)
└── services/
    ├── whatsapp.py       ← WhatsApp Cloud API client
    └── ai_intent.py      ← NL intent classifier
tests/
```

## Development setup

```bash
# 1. Clone
git clone https://github.com/tegaconsults-cloud/mazi-idika.git
cd mazi-idika

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env with your Supabase, Paystack, and WhatsApp credentials

# 5. Run dev server
uvicorn app.main:app --reload --port 8000
```

API docs available at: http://localhost:8000/docs

## Development phases

| Phase | Scope |
|---|---|
| 1 | Auth, business onboarding, inventory, sales, dashboard |
| 2 | POS, expenses, debts, financial reports |
| 3 | Staff management, suppliers, purchase orders |
| 4 | AI assistant, voice notes, Nigerian language support |
| 5 | Offline sync, loyalty, multi-branch |
| 6 | Predictive analytics, advanced BI |

## Critical architecture notes

- **Multi-tenant:** every DB row is scoped to `business_id`. Supabase RLS enforces this at the database layer.
- **Offline-safe transactions:** every sale carries a client-generated `tx_id`. Server uses `INSERT ... ON CONFLICT (tx_id) DO NOTHING` — safe to replay.
- **Audit trail:** every mutation on Business tier writes to `audit_logs`. Never deleted.
- **Soft deletes:** products and customers are never hard-deleted. `is_deleted` flag + RLS filter.
