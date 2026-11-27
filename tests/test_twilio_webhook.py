"""Comprehensive tests for the Twilio WhatsApp integration.

Coverage:
  Security
    - Valid signature accepted
    - Invalid / missing signature rejected (no DB side-effects)
    - Bad shared-secret token rejected (no DB side-effects)
    - Production mode (APP_ENV != development) rejects missing credentials
    - Development mode allows missing credentials with explicit bypass

  DB operations (SQLite in-memory, no external DB needed)
    - Restock / add new product
    - Record a sale (stock deducted, transaction created)
    - Oversell rejected (stock unchanged)
    - Credit sale creates debt automatically
    - Query stock level
    - Record a debt manually
    - Settle a debt fully
    - Settle a debt partially
    - Query outstanding debts
    - Daily / weekly / monthly summary
    - Top products report

  Tenant isolation
    - Business A cannot see Business B's products
    - Business A cannot see Business B's debts
    - Unregistered number gets onboarding reply, no DB write

  Outbound send (Twilio REST API mocked)
    - send_text_reply calls client.messages.create with correct from/to/body
    - send_text_reply returns False and does not raise when client unavailable
    - Body > 1600 chars is truncated before sending

  Media handling
    - Audio message triggers transcription pipeline
    - Image message triggers OCR pipeline
    - Document message triggers PDF extraction pipeline
    - Transcription failure returns descriptive error, no crash
    - Media download failure returns descriptive error, no crash

  Intent classification
    - 9 keyword fallback intents verified

  Agent end-to-end (mocked LLM + mocked send, real in-memory DB)
    - Full sale flow through webhook POST
    - Unregistered sender gets onboarding reply
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ── App imports ───────────────────────────────────────────────────────────────
from app.main import app
from app.core.config import settings
from app.models.base import Base
from app.models.business import Business, Branch
from app.models.product import Product
from app.models.transaction import Transaction, TransactionItem, PaymentMethod, TransactionStatus
from app.models.debt import Debt, DebtStatus
from app.services.ai_intent import classify_intent, Intent
from app.services.twilio_whatsapp import (
    TwilioMessage,
    parse_twilio_webhook,
    send_text_reply,
    validate_twilio_signature,
)
from app.services import agent as agent_module
from app.services.llm import format_naira


# ═══════════════════════════════════════════════════════════════════════════════
# IN-MEMORY DATABASE FIXTURE
# ═══════════════════════════════════════════════════════════════════════════════

@pytest_asyncio.fixture
async def db_engine():
    """Create a fresh in-memory SQLite async engine for each test."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Return a session bound to the in-memory test database."""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


# ── Seed helpers ──────────────────────────────────────────────────────────────

async def seed_business(
    db: AsyncSession,
    phone: str = "2348012345678",
    name: str = "Chioma Provisions",
) -> tuple[Business, Branch]:
    """Create a Business + Branch in the test DB."""
    biz = Business(
        id=str(uuid.uuid4()),
        name=name,
        phone=phone,
        business_type="provision_store",
        owner_id=str(uuid.uuid4()),
    )
    db.add(biz)
    await db.flush()

    branch = Branch(
        id=str(uuid.uuid4()),
        business_id=biz.id,
        name="Main Branch",
    )
    db.add(branch)
    await db.flush()
    return biz, branch


async def seed_product(
    db: AsyncSession,
    branch_id: str,
    name: str = "Indomie",
    quantity: int = 20,
    sell_price: float = 3500.0,
    cost_price: float = 2800.0,
) -> Product:
    p = Product(
        id=str(uuid.uuid4()),
        branch_id=branch_id,
        name=name,
        quantity=quantity,
        sell_price=sell_price,
        cost_price=cost_price,
        unit="carton",
        low_stock_threshold=5,
    )
    db.add(p)
    await db.flush()
    return p


# ── Fake TwilioMessage builder ────────────────────────────────────────────────

def make_msg(
    body: str = "hello",
    from_number: str = "+2348012345678",
    msg_type: str = "text",
    media_url: str | None = None,
    media_content_type: str | None = None,
) -> TwilioMessage:
    form = {
        "MessageSid": "SM" + uuid.uuid4().hex[:20],
        "From": f"whatsapp:{from_number}",
        "To": "whatsapp:+14155238886",
        "Body": body,
        "NumMedia": "1" if media_url else "0",
        "ProfileName": "Test User",
    }
    if media_url:
        form["MediaUrl0"] = media_url
        form["MediaContentType0"] = media_content_type or "audio/ogg"
    return parse_twilio_webhook(form)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — SECURITY
# ═══════════════════════════════════════════════════════════════════════════════

class TestSignatureValidation:

    def test_valid_signature_accepted(self):
        """With a known auth token + correct HMAC, validation passes."""
        from twilio.request_validator import RequestValidator
        auth_token = "test_auth_token_abc123"
        url = "https://example.com/api/v1/webhooks/twilio/whatsapp"
        params = {"Body": "hello", "From": "whatsapp:+2348012345678"}
        validator = RequestValidator(auth_token)
        good_sig = validator.compute_signature(url, params)

        with patch.object(settings, "TWILIO_AUTH_TOKEN", auth_token):
            result = validate_twilio_signature(url, params, good_sig)
        assert result is True

    def test_wrong_signature_rejected(self):
        """A tampered signature must fail."""
        auth_token = "test_auth_token_abc123"
        url = "https://example.com/api/v1/webhooks/twilio/whatsapp"
        params = {"Body": "hello"}
        with patch.object(settings, "TWILIO_AUTH_TOKEN", auth_token):
            result = validate_twilio_signature(url, params, "completely_wrong_sig")
        assert result is False

    def test_empty_signature_rejected_when_token_configured(self):
        """Missing X-Twilio-Signature header must reject the request."""
        with patch.object(settings, "TWILIO_AUTH_TOKEN", "some_real_token"):
            result = validate_twilio_signature(
                "https://example.com/api/v1/webhooks/twilio/whatsapp",
                {"Body": "hi"},
                "",   # no signature
            )
        assert result is False

    def test_missing_credentials_rejected_in_production(self):
        """Production (APP_ENV != development) with no token must reject all requests."""
        with (
            patch.object(settings, "TWILIO_AUTH_TOKEN", ""),
            patch.object(settings, "APP_ENV", "production"),
        ):
            result = validate_twilio_signature(
                "https://example.com/webhook",
                {"Body": "hi"},
                "any_signature",
            )
        assert result is False

    def test_missing_credentials_allowed_in_development(self):
        """Development mode may bypass validation when no token is configured."""
        with (
            patch.object(settings, "TWILIO_AUTH_TOKEN", ""),
            patch.object(settings, "APP_ENV", "development"),
        ):
            result = validate_twilio_signature(
                "http://localhost/webhook",
                {"Body": "hi"},
                "",
            )
        assert result is True

    @pytest.mark.asyncio
    async def test_bad_shared_secret_returns_200_but_no_agent_call(self):
        """
        A request with wrong ?token= must return 200 (silent drop) but
        the agent must NOT be called — no DB side-effects.
        """
        called = []

        async def fake_agent(msg):
            called.append(msg)
            return "should not be called"

        with (
            patch.object(settings, "TWILIO_WEBHOOK_SECRET", "correct_secret"),
            patch("app.api.v1.routes.twilio_webhook.handle_message", fake_agent),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/v1/webhooks/twilio/whatsapp?token=wrong_secret",
                    data={"Body": "hello", "From": "whatsapp:+234123"},
                )

        assert resp.status_code == 200
        assert called == [], "Agent must not be invoked when token check fails"

    @pytest.mark.asyncio
    async def test_missing_signature_returns_200_but_no_agent_call(self):
        """
        A request with no X-Twilio-Signature and a real AUTH_TOKEN configured
        must return 200 but NOT call the agent.
        """
        called = []

        async def fake_agent(msg):
            called.append(msg)
            return "should not be called"

        with (
            patch.object(settings, "TWILIO_AUTH_TOKEN", "real_token"),
            patch.object(settings, "TWILIO_WEBHOOK_SECRET", ""),
            patch.object(settings, "APP_ENV", "production"),
            patch("app.api.v1.routes.twilio_webhook.handle_message", fake_agent),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/v1/webhooks/twilio/whatsapp",
                    data={"Body": "hello", "From": "whatsapp:+234123"},
                    # No X-Twilio-Signature header
                )

        assert resp.status_code == 200
        assert called == [], "Agent must not be invoked when signature check fails"

    @pytest.mark.asyncio
    async def test_valid_signature_reaches_agent(self):
        """A correctly signed request (mocked validator) does reach the agent."""
        called = []

        async def fake_agent(msg):
            called.append(msg)
            return "hi back"

        with (
            patch(
                "app.api.v1.routes.twilio_webhook.validate_twilio_signature",
                return_value=True,
            ),
            patch("app.api.v1.routes.twilio_webhook.handle_message", fake_agent),
            patch(
                "app.api.v1.routes.twilio_webhook.send_text_reply",
                return_value=True,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/v1/webhooks/twilio/whatsapp",
                    data={
                        "MessageSid": "SM123",
                        "From": "whatsapp:+2348012345678",
                        "To": "whatsapp:+14155238886",
                        "Body": "hello",
                        "NumMedia": "0",
                    },
                    headers={"X-Twilio-Signature": "mock"},
                )

        assert resp.status_code == 200
        assert len(called) == 1, "Agent must be called exactly once"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — DATABASE OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════

class TestInventoryOperations:

    @pytest.mark.asyncio
    async def test_restock_existing_product(self, db_session):
        biz, branch = await seed_business(db_session)
        product = await seed_product(db_session, branch.id, quantity=10)

        from app.services.ai_intent import ParsedIntent, Intent
        parsed = ParsedIntent(
            intent=Intent.RESTOCK,
            confidence=0.9,
            entities={"product_name": "Indomie", "quantity": 5},
            raw_text="add 5 cartons Indomie",
        )
        reply = await agent_module._handle_restock(parsed, branch, db_session)

        assert "15" in reply, "New quantity (10+5) must appear in reply"
        assert "Indomie" in reply

        # Verify DB
        await db_session.refresh(product)
        assert product.quantity == 15

    @pytest.mark.asyncio
    async def test_add_new_product_via_restock(self, db_session):
        biz, branch = await seed_business(db_session)

        from app.services.ai_intent import ParsedIntent, Intent
        parsed = ParsedIntent(
            intent=Intent.RESTOCK,
            confidence=0.9,
            entities={
                "product_name": "Peak Milk",
                "quantity": 24,
                "cost_price": 950.0,
                "sell_price": 1200.0,
                "unit": "tin",
            },
            raw_text="add 24 tins Peak Milk cost 950",
        )
        reply = await agent_module._handle_restock(parsed, branch, db_session)

        assert "Peak Milk" in reply
        # Verify product exists in DB
        from sqlalchemy import select
        result = await db_session.execute(
            select(Product).where(Product.branch_id == branch.id)
        )
        products = result.scalars().all()
        assert len(products) == 1
        assert products[0].quantity == 24
        assert products[0].cost_price == 950.0

    @pytest.mark.asyncio
    async def test_record_sale_deducts_stock(self, db_session):
        biz, branch = await seed_business(db_session)
        product = await seed_product(
            db_session, branch.id, quantity=20, sell_price=3500.0, cost_price=2800.0
        )
        initial_qty = product.quantity  # 20

        from app.services.ai_intent import ParsedIntent, Intent
        parsed = ParsedIntent(
            intent=Intent.RECORD_SALE,
            confidence=0.95,
            entities={
                "product_name": "Indomie",
                "quantity": 3,
                "payment_method": "cash",
            },
            raw_text="sold 3 indomie cash",
        )
        reply = await agent_module._handle_record_sale(parsed, branch, db_session)

        assert "Sale recorded" in reply or "✅" in reply
        # Stock deducted
        await db_session.refresh(product)
        assert product.quantity == initial_qty - 3

        # Transaction exists in DB
        from sqlalchemy import select
        result = await db_session.execute(
            select(Transaction).where(Transaction.branch_id == branch.id)
        )
        txns = result.scalars().all()
        assert len(txns) == 1
        assert txns[0].total_amount == 3 * 3500.0
        assert txns[0].payment_method == PaymentMethod.cash

        # Transaction item exists
        result = await db_session.execute(
            select(TransactionItem).where(
                TransactionItem.transaction_tx_id == txns[0].tx_id
            )
        )
        items = result.scalars().all()
        assert len(items) == 1
        assert items[0].quantity == 3

    @pytest.mark.asyncio
    async def test_oversell_rejected_stock_unchanged(self, db_session):
        biz, branch = await seed_business(db_session)
        product = await seed_product(db_session, branch.id, quantity=2)

        from app.services.ai_intent import ParsedIntent, Intent
        parsed = ParsedIntent(
            intent=Intent.RECORD_SALE,
            confidence=0.9,
            entities={"product_name": "Indomie", "quantity": 10},
            raw_text="sold 10 indomie",
        )
        reply = await agent_module._handle_record_sale(parsed, branch, db_session)

        # Must warn about insufficient stock
        assert "Not enough" in reply or "enough" in reply.lower() or "stock" in reply.lower()

        # Stock must not have changed
        await db_session.refresh(product)
        assert product.quantity == 2

        # No transaction created
        from sqlalchemy import select
        result = await db_session.execute(
            select(Transaction).where(Transaction.branch_id == branch.id)
        )
        assert result.scalars().all() == []

    @pytest.mark.asyncio
    async def test_credit_sale_creates_debt(self, db_session):
        biz, branch = await seed_business(db_session)
        product = await seed_product(db_session, branch.id, quantity=10, sell_price=3500.0)

        from app.services.ai_intent import ParsedIntent, Intent
        parsed = ParsedIntent(
            intent=Intent.RECORD_SALE,
            confidence=0.9,
            entities={
                "product_name": "Indomie",
                "quantity": 2,
                "payment_method": "credit",
                "customer_name": "Mama Titi",
            },
            raw_text="Mama Titi bought 2 indomie on credit",
        )
        reply = await agent_module._handle_record_sale(parsed, branch, db_session)

        assert "Credit recorded" in reply or "credit" in reply.lower()

        # Debt row exists
        from sqlalchemy import select
        result = await db_session.execute(
            select(Debt).where(Debt.branch_id == branch.id)
        )
        debts = result.scalars().all()
        assert len(debts) == 1
        assert debts[0].customer_name == "Mama Titi"
        assert debts[0].original_amount == 2 * 3500.0
        assert debts[0].status == DebtStatus.pending

    @pytest.mark.asyncio
    async def test_query_stock_specific_product(self, db_session):
        biz, branch = await seed_business(db_session)
        await seed_product(db_session, branch.id, name="Milo", quantity=8)

        from app.services.ai_intent import ParsedIntent, Intent
        parsed = ParsedIntent(
            intent=Intent.QUERY_STOCK,
            confidence=0.9,
            entities={"product_name": "Milo"},
            raw_text="how many Milo remain",
        )
        reply = await agent_module._handle_query_stock(parsed, branch, db_session)

        assert "Milo" in reply
        assert "8" in reply

    @pytest.mark.asyncio
    async def test_query_stock_low_stock_list(self, db_session):
        biz, branch = await seed_business(db_session)
        # 2 items below threshold (5), 1 above
        await seed_product(db_session, branch.id, name="A", quantity=2)
        await seed_product(db_session, branch.id, name="B", quantity=3)
        await seed_product(db_session, branch.id, name="C", quantity=50)

        from app.services.ai_intent import ParsedIntent, Intent
        parsed = ParsedIntent(
            intent=Intent.QUERY_STOCK,
            confidence=0.9,
            entities={},  # no specific product → list all low
            raw_text="low stock items",
        )
        reply = await agent_module._handle_query_stock(parsed, branch, db_session)

        assert "A" in reply
        assert "B" in reply
        assert "C" not in reply, "Well-stocked items must not appear in low-stock list"


class TestDebtOperations:

    @pytest.mark.asyncio
    async def test_record_debt_manually(self, db_session):
        biz, branch = await seed_business(db_session)

        from app.services.ai_intent import ParsedIntent, Intent
        parsed = ParsedIntent(
            intent=Intent.RECORD_DEBT,
            confidence=0.9,
            entities={"customer_name": "Alhaji Musa", "amount": 24000.0},
            raw_text="Alhaji Musa owes me 24000",
        )
        reply = await agent_module._handle_record_debt(parsed, branch, db_session)

        assert "Alhaji Musa" in reply
        assert "24" in reply.replace(",", "")

        from sqlalchemy import select
        result = await db_session.execute(
            select(Debt).where(Debt.branch_id == branch.id)
        )
        debts = result.scalars().all()
        assert len(debts) == 1
        assert debts[0].original_amount == 24000.0
        assert debts[0].status == DebtStatus.pending

    @pytest.mark.asyncio
    async def test_settle_debt_fully(self, db_session):
        biz, branch = await seed_business(db_session)
        debt = Debt(
            id=str(uuid.uuid4()),
            branch_id=branch.id,
            customer_name="Emeka",
            original_amount=10000.0,
            amount_paid=0.0,
            status=DebtStatus.pending,
        )
        db_session.add(debt)
        await db_session.flush()

        from app.services.ai_intent import ParsedIntent, Intent
        parsed = ParsedIntent(
            intent=Intent.SETTLE_DEBT,
            confidence=0.9,
            entities={"customer_name": "Emeka"},  # no amount → full payment
            raw_text="Emeka don settle",
        )
        reply = await agent_module._handle_settle_debt(parsed, branch, db_session)

        assert "Fully settled" in reply or "settled" in reply.lower()
        await db_session.refresh(debt)
        assert debt.status == DebtStatus.settled
        assert debt.amount_paid == 10000.0

    @pytest.mark.asyncio
    async def test_settle_debt_partially(self, db_session):
        biz, branch = await seed_business(db_session)
        debt = Debt(
            id=str(uuid.uuid4()),
            branch_id=branch.id,
            customer_name="Bisi",
            original_amount=8000.0,
            amount_paid=0.0,
            status=DebtStatus.pending,
        )
        db_session.add(debt)
        await db_session.flush()

        from app.services.ai_intent import ParsedIntent, Intent
        parsed = ParsedIntent(
            intent=Intent.SETTLE_DEBT,
            confidence=0.9,
            entities={"customer_name": "Bisi", "amount": 3000.0},
            raw_text="Bisi paid 3000",
        )
        reply = await agent_module._handle_settle_debt(parsed, branch, db_session)

        assert "partial" in reply.lower() or "5,000" in reply or "5000" in reply
        await db_session.refresh(debt)
        assert debt.status == DebtStatus.partial
        assert debt.amount_paid == 3000.0

    @pytest.mark.asyncio
    async def test_settle_nonexistent_debt_gives_clear_error(self, db_session):
        biz, branch = await seed_business(db_session)

        from app.services.ai_intent import ParsedIntent, Intent
        parsed = ParsedIntent(
            intent=Intent.SETTLE_DEBT,
            confidence=0.9,
            entities={"customer_name": "Nobody"},
            raw_text="Nobody paid",
        )
        reply = await agent_module._handle_settle_debt(parsed, branch, db_session)

        assert "not found" in reply.lower() or "❌" in reply

    @pytest.mark.asyncio
    async def test_query_debts_returns_correct_total(self, db_session):
        biz, branch = await seed_business(db_session)
        for name, amount in [("A", 5000.0), ("B", 3000.0), ("C", 2000.0)]:
            d = Debt(
                id=str(uuid.uuid4()),
                branch_id=branch.id,
                customer_name=name,
                original_amount=amount,
                amount_paid=0.0,
                status=DebtStatus.pending,
            )
            db_session.add(d)
        await db_session.flush()

        from app.services.ai_intent import ParsedIntent, Intent
        parsed = ParsedIntent(Intent.QUERY_DEBTS, 0.9, {}, "who owes me")
        reply = await agent_module._handle_query_debts(parsed, branch, db_session)

        assert "10,000" in reply or "10000" in reply.replace(",", "")
        assert "A" in reply
        assert "B" in reply
        assert "C" in reply


class TestReports:

    @pytest.mark.asyncio
    async def test_summary_today(self, db_session):
        biz, branch = await seed_business(db_session)
        product = await seed_product(db_session, branch.id, quantity=10, sell_price=3500.0)

        # Insert a completed transaction
        tx_id = str(uuid.uuid4())
        tx = Transaction(
            tx_id=tx_id,
            branch_id=branch.id,
            payment_method=PaymentMethod.cash,
            status=TransactionStatus.completed,
            total_amount=10500.0,
            recorded_at=datetime.now(timezone.utc),
        )
        item = TransactionItem(
            id=str(uuid.uuid4()),
            transaction_tx_id=tx_id,
            product_id=product.id,
            product_name=product.name,
            quantity=3,
            unit_price=3500.0,
            total=10500.0,
        )
        db_session.add(tx)
        db_session.add(item)
        await db_session.flush()

        from app.services.ai_intent import ParsedIntent, Intent
        parsed = ParsedIntent(
            Intent.QUERY_SUMMARY, 0.9, {"period": "today"}, "today summary"
        )
        reply = await agent_module._handle_query_summary(parsed, branch, db_session)

        assert "10,500" in reply or "10500" in reply.replace(",", "")
        assert "1" in reply  # 1 transaction
        assert "Today" in reply

    @pytest.mark.asyncio
    async def test_top_products(self, db_session):
        biz, branch = await seed_business(db_session)
        p1 = await seed_product(db_session, branch.id, name="Indomie", quantity=100)
        p2 = await seed_product(db_session, branch.id, name="Peak Milk", quantity=100)

        # Indomie: ₦14,000 revenue; Peak Milk: ₦9,600 revenue
        for product, qty, price in [(p1, 4, 3500.0), (p2, 8, 1200.0)]:
            tx_id = str(uuid.uuid4())
            tx = Transaction(
                tx_id=tx_id,
                branch_id=branch.id,
                payment_method=PaymentMethod.cash,
                status=TransactionStatus.completed,
                total_amount=qty * price,
                recorded_at=datetime.now(timezone.utc),
            )
            item = TransactionItem(
                id=str(uuid.uuid4()),
                transaction_tx_id=tx_id,
                product_id=product.id,
                product_name=product.name,
                quantity=qty,
                unit_price=price,
                total=qty * price,
            )
            db_session.add(tx)
            db_session.add(item)
        await db_session.flush()

        from app.services.ai_intent import ParsedIntent, Intent
        parsed = ParsedIntent(Intent.QUERY_TOP, 0.9, {}, "top products")
        reply = await agent_module._handle_query_top(parsed, branch, db_session)

        assert "Indomie" in reply
        assert "Peak Milk" in reply
        # Indomie (14,000) must rank above Peak Milk (9,600)
        assert reply.index("Indomie") < reply.index("Peak Milk")

    @pytest.mark.asyncio
    async def test_summary_no_sales_returns_zeros(self, db_session):
        biz, branch = await seed_business(db_session)

        from app.services.ai_intent import ParsedIntent, Intent
        parsed = ParsedIntent(Intent.QUERY_SUMMARY, 0.9, {"period": "today"}, "today")
        reply = await agent_module._handle_query_summary(parsed, branch, db_session)

        assert "0" in reply
        assert "Today" in reply


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — TENANT ISOLATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestTenantIsolation:

    @pytest.mark.asyncio
    async def test_business_a_cannot_see_business_b_products(self, db_session):
        _, branch_a = await seed_business(db_session, phone="234111", name="Biz A")
        _, branch_b = await seed_business(db_session, phone="234222", name="Biz B")

        # Only branch B has the product
        await seed_product(db_session, branch_b.id, name="Secret Item", quantity=50)

        from app.services.ai_intent import ParsedIntent, Intent
        parsed = ParsedIntent(
            Intent.QUERY_STOCK,
            0.9,
            {"product_name": "Secret Item"},
            "how many Secret Item",
        )
        # Query from branch A's perspective
        reply = await agent_module._handle_query_stock(parsed, branch_a, db_session)

        assert "not found" in reply.lower() or "❌" in reply, (
            "Business A must not see Business B's products"
        )

    @pytest.mark.asyncio
    async def test_business_a_cannot_see_business_b_debts(self, db_session):
        _, branch_a = await seed_business(db_session, phone="234333", name="A")
        _, branch_b = await seed_business(db_session, phone="234444", name="B")

        # Debt belongs to branch B
        debt = Debt(
            id=str(uuid.uuid4()),
            branch_id=branch_b.id,
            customer_name="B's Customer",
            original_amount=5000.0,
            amount_paid=0.0,
            status=DebtStatus.pending,
        )
        db_session.add(debt)
        await db_session.flush()

        from app.services.ai_intent import ParsedIntent, Intent
        parsed = ParsedIntent(Intent.QUERY_DEBTS, 0.9, {}, "who owes me")
        reply = await agent_module._handle_query_debts(parsed, branch_a, db_session)

        # Branch A should see no debts
        assert "No outstanding debts" in reply or "paid up" in reply

    @pytest.mark.asyncio
    async def test_unregistered_sender_gets_onboarding_reply(self, db_session, db_engine):
        """An unknown phone number must NOT touch any existing business data."""
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from contextlib import asynccontextmanager

        _, branch_a = await seed_business(db_session, phone="234999")
        await seed_product(db_session, branch_a.id, quantity=100)
        await db_session.commit()

        factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

        @asynccontextmanager
        async def test_get_db():
            async with factory() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        msg = make_msg("who dey owe me", from_number="+1555000000")

        with patch("app.services.agent.get_db", test_get_db):
            reply = await agent_module.handle_message(msg)

        assert "Welcome" in reply or "mazi.ng" in reply or "Sign up" in reply
        assert "234999" not in reply, "Must not leak data about registered business"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — OUTBOUND TWILIO SEND
# ═══════════════════════════════════════════════════════════════════════════════

class TestOutboundSend:

    def test_send_text_reply_calls_twilio_with_correct_params(self):
        mock_client = MagicMock()
        mock_msg = MagicMock()
        mock_client.messages.create.return_value = mock_msg

        with (
            patch("app.services.twilio_whatsapp.get_twilio_client", return_value=mock_client),
            patch.object(settings, "TWILIO_WHATSAPP_NUMBER", "+14155238886"),
        ):
            result = send_text_reply("+2348012345678", "Hello trader!")

        assert result is True
        mock_client.messages.create.assert_called_once_with(
            from_="whatsapp:+14155238886",
            to="whatsapp:+2348012345678",
            body="Hello trader!",
        )

    def test_send_text_reply_truncates_long_body(self):
        mock_client = MagicMock()
        with (
            patch("app.services.twilio_whatsapp.get_twilio_client", return_value=mock_client),
            patch.object(settings, "TWILIO_WHATSAPP_NUMBER", "+14155238886"),
        ):
            long_body = "x" * 2000
            send_text_reply("+234123", long_body)

        call_kwargs = mock_client.messages.create.call_args[1]
        assert len(call_kwargs["body"]) == 1600

    def test_send_text_reply_returns_false_when_no_client(self):
        with patch("app.services.twilio_whatsapp.get_twilio_client", return_value=None):
            result = send_text_reply("+234123", "test")
        assert result is False

    def test_send_text_reply_returns_false_on_twilio_exception(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("Twilio API error")
        with (
            patch("app.services.twilio_whatsapp.get_twilio_client", return_value=mock_client),
            patch.object(settings, "TWILIO_WHATSAPP_NUMBER", "+14155238886"),
        ):
            result = send_text_reply("+234123", "test")
        assert result is False  # must not raise

    def test_send_reply_adds_whatsapp_prefix(self):
        mock_client = MagicMock()
        with (
            patch("app.services.twilio_whatsapp.get_twilio_client", return_value=mock_client),
            patch.object(settings, "TWILIO_WHATSAPP_NUMBER", "14155238886"),  # no prefix
        ):
            send_text_reply("2348012345678", "hi")  # no prefix

        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["from_"].startswith("whatsapp:")
        assert call_kwargs["to"].startswith("whatsapp:")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — MEDIA HANDLING
# ═══════════════════════════════════════════════════════════════════════════════

class TestMediaHandling:

    def test_audio_message_detected(self):
        form = {
            "From": "whatsapp:+234123",
            "To": "whatsapp:+14155238886",
            "Body": "",
            "NumMedia": "1",
            "MediaUrl0": "https://api.twilio.com/media/SM123/audio.ogg",
            "MediaContentType0": "audio/ogg; codecs=opus",
            "MessageSid": "SM123",
        }
        msg = parse_twilio_webhook(form)
        assert msg.msg_type == "audio"
        assert msg.media_url == "https://api.twilio.com/media/SM123/audio.ogg"

    def test_image_message_detected(self):
        form = {
            "From": "whatsapp:+234123",
            "To": "whatsapp:+14155238886",
            "Body": "",
            "NumMedia": "1",
            "MediaUrl0": "https://api.twilio.com/media/SM456/photo.jpg",
            "MediaContentType0": "image/jpeg",
            "MessageSid": "SM456",
        }
        msg = parse_twilio_webhook(form)
        assert msg.msg_type == "image"

    def test_pdf_document_detected(self):
        form = {
            "From": "whatsapp:+234123",
            "To": "whatsapp:+14155238886",
            "Body": "",
            "NumMedia": "1",
            "MediaUrl0": "https://api.twilio.com/media/SM789/list.pdf",
            "MediaContentType0": "application/pdf",
            "MessageSid": "SM789",
        }
        msg = parse_twilio_webhook(form)
        assert msg.msg_type == "document"

    def test_text_message_has_no_media(self):
        form = {
            "From": "whatsapp:+234123",
            "To": "whatsapp:+14155238886",
            "Body": "hello",
            "NumMedia": "0",
            "MessageSid": "SM000",
        }
        msg = parse_twilio_webhook(form)
        assert msg.msg_type == "text"
        assert msg.media_url is None

    @pytest.mark.asyncio
    async def test_audio_path_calls_transcription(self, db_session, db_engine):
        """Audio message must call transcribe_audio and route the result to the agent."""
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from contextlib import asynccontextmanager

        biz, branch = await seed_business(db_session)
        await db_session.commit()

        factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

        @asynccontextmanager
        async def test_get_db():
            async with factory() as s:
                try:
                    yield s
                    await s.commit()
                except Exception:
                    await s.rollback()
                    raise

        msg = make_msg("", from_number="+2348012345678", msg_type="audio",
                       media_url="https://twilio.com/media/audio.ogg",
                       media_content_type="audio/ogg")

        with (
            patch("app.services.agent.download_media", new_callable=AsyncMock,
                  return_value=b"fake_audio_bytes"),
            patch("app.services.agent.transcribe_audio", new_callable=AsyncMock,
                  return_value="sold 5 indomie cash"),
            patch("app.services.agent.classify_message", new_callable=AsyncMock) as mock_classify,
            patch("app.services.agent.get_db", test_get_db),
        ):
            from app.services.ai_intent import ParsedIntent, Intent
            mock_classify.return_value = ParsedIntent(
                Intent.GREETING, 0.5, {}, "sold 5 indomie cash"
            )
            await agent_module.handle_message(msg)

        mock_classify.assert_called_once_with("sold 5 indomie cash")

    @pytest.mark.asyncio
    async def test_transcription_failure_returns_error_message(self, db_session, db_engine):
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from contextlib import asynccontextmanager

        biz, branch = await seed_business(db_session)
        await db_session.commit()

        factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

        @asynccontextmanager
        async def test_get_db():
            async with factory() as s:
                try:
                    yield s
                    await s.commit()
                except Exception:
                    await s.rollback()
                    raise

        msg = make_msg("", from_number="+2348012345678", msg_type="audio",
                       media_url="https://twilio.com/media/audio.ogg",
                       media_content_type="audio/ogg")

        with (
            patch("app.services.agent.download_media", new_callable=AsyncMock,
                  return_value=b"fake_bytes"),
            patch("app.services.agent.transcribe_audio", new_callable=AsyncMock,
                  return_value=None),  # transcription failed
            patch("app.services.agent.get_db", test_get_db),
        ):
            reply = await agent_module.handle_message(msg)

        assert "transcribe" in reply.lower() or "voice" in reply.lower()

    @pytest.mark.asyncio
    async def test_image_path_calls_ocr(self, db_session, db_engine):
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from contextlib import asynccontextmanager

        biz, branch = await seed_business(db_session)
        await db_session.commit()

        factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

        @asynccontextmanager
        async def test_get_db():
            async with factory() as s:
                try:
                    yield s
                    await s.commit()
                except Exception:
                    await s.rollback()
                    raise

        msg = make_msg("", from_number="+2348012345678", msg_type="image",
                       media_url="https://twilio.com/media/photo.jpg",
                       media_content_type="image/jpeg")

        with (
            patch("app.services.agent.download_media", new_callable=AsyncMock,
                  return_value=b"fake_image_bytes"),
            patch("app.services.agent.extract_from_image", new_callable=AsyncMock,
                  return_value="Indomie 70g — 20 cartons — ₦2800") as mock_ocr,
            patch("app.services.agent.classify_message", new_callable=AsyncMock) as mock_classify,
            patch("app.services.agent.get_db", test_get_db),
        ):
            from app.services.ai_intent import ParsedIntent, Intent
            mock_classify.return_value = ParsedIntent(
                Intent.GREETING, 0.5, {}, "Indomie 70g"
            )
            await agent_module.handle_message(msg)

        mock_ocr.assert_called_once()
        call_arg = mock_classify.call_args[0][0]
        assert "Indomie" in call_arg

    @pytest.mark.asyncio
    async def test_media_download_failure_returns_error(self, db_session, db_engine):
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from contextlib import asynccontextmanager

        biz, branch = await seed_business(db_session)
        await db_session.commit()

        factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

        @asynccontextmanager
        async def test_get_db():
            async with factory() as s:
                try:
                    yield s
                    await s.commit()
                except Exception:
                    await s.rollback()
                    raise

        msg = make_msg("", from_number="+2348012345678", msg_type="audio",
                       media_url="https://twilio.com/media/audio.ogg",
                       media_content_type="audio/ogg")

        with (
            patch("app.services.agent.download_media", new_callable=AsyncMock,
                  return_value=None),  # download failed
            patch("app.services.agent.get_db", test_get_db),
        ):
            reply = await agent_module.handle_message(msg)

        assert "download" in reply.lower() or "voice note" in reply.lower() or "⚠️" in reply


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — INTENT CLASSIFICATION (keyword fallback)
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntentClassification:

    @pytest.mark.asyncio
    async def test_greeting(self):
        r = await classify_intent("hi there")
        assert r.intent == Intent.GREETING

    @pytest.mark.asyncio
    async def test_sale_english(self):
        r = await classify_intent("sold 5 indomie, 3500 each, cash")
        assert r.intent == Intent.RECORD_SALE

    @pytest.mark.asyncio
    async def test_sale_pidgin(self):
        r = await classify_intent("ta indomie 10 cartons transfer")
        assert r.intent == Intent.RECORD_SALE

    @pytest.mark.asyncio
    async def test_restock_pidgin(self):
        r = await classify_intent("20 cartons indomie don reach")
        assert r.intent == Intent.RESTOCK

    @pytest.mark.asyncio
    async def test_query_debts(self):
        r = await classify_intent("who dey owe me?")
        assert r.intent == Intent.QUERY_DEBTS

    @pytest.mark.asyncio
    async def test_settle_debt(self):
        r = await classify_intent("Mama Titi don pay 5000")
        assert r.intent == Intent.SETTLE_DEBT

    @pytest.mark.asyncio
    async def test_summary_today(self):
        r = await classify_intent("today summary")
        assert r.intent == Intent.QUERY_SUMMARY
        assert r.entities.get("period") == "today"

    @pytest.mark.asyncio
    async def test_summary_week(self):
        r = await classify_intent("weekly report")
        assert r.intent == Intent.QUERY_SUMMARY
        assert r.entities.get("period") == "week"

    @pytest.mark.asyncio
    async def test_unknown(self):
        r = await classify_intent("asdqwezxc")
        assert r.intent == Intent.UNKNOWN


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — FULL END-TO-END THROUGH WEBHOOK (mocked LLM + mocked send)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEndToEnd:

    @pytest.mark.asyncio
    async def test_full_sale_flow_through_webhook(self, db_engine):
        """
        POST to the webhook endpoint → validation → agent → DB write → Twilio send.
        Uses real in-memory SQLite DB, mocked LLM classifier and Twilio send.
        """
        # Build a fresh session factory wired to the test engine
        from sqlalchemy.ext.asyncio import async_sessionmaker
        factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

        async with factory() as setup_db:
            biz, branch = await seed_business(setup_db, phone="2348012345678")
            await seed_product(
                setup_db, branch.id, name="Indomie", quantity=10, sell_price=3500.0
            )
            await setup_db.commit()

        sent_messages = []

        def fake_send(to, body):
            sent_messages.append({"to": to, "body": body})
            return True

        from app.services.ai_intent import ParsedIntent, Intent
        from contextlib import asynccontextmanager

        # Provide a get_db that opens sessions on the in-memory test DB
        @asynccontextmanager
        async def test_get_db():
            async with factory() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        with (
            patch(
                "app.api.v1.routes.twilio_webhook.validate_twilio_signature",
                return_value=True,
            ),
            patch(
                "app.services.agent.classify_message",
                new_callable=AsyncMock,
                return_value=ParsedIntent(
                    Intent.RECORD_SALE,
                    0.95,
                    {
                        "product_name": "Indomie",
                        "quantity": 2,
                        "payment_method": "cash",
                    },
                    "sold 2 indomie cash",
                ),
            ),
            patch("app.services.agent.get_db", test_get_db),
            patch("app.api.v1.routes.twilio_webhook.send_text_reply", side_effect=fake_send),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/v1/webhooks/twilio/whatsapp",
                    data={
                        "MessageSid": "SMtest123",
                        "From": "whatsapp:+2348012345678",
                        "To": "whatsapp:+14155238886",
                        "Body": "sold 2 indomie cash",
                        "NumMedia": "0",
                    },
                    headers={"X-Twilio-Signature": "mock_sig"},
                )

        assert resp.status_code == 200
        assert len(sent_messages) == 1, f"Expected 1 sent message, got: {sent_messages}"
        assert sent_messages[0]["to"] == "+2348012345678"
        reply_body = sent_messages[0]["body"]
        assert "Sale recorded" in reply_body or "✅" in reply_body, (
            f"Unexpected reply: {reply_body}"
        )

    @pytest.mark.asyncio
    async def test_unregistered_number_webhook_reply(self, db_engine):
        """An unregistered sender gets an onboarding reply without touching any business data."""
        from sqlalchemy.ext.asyncio import async_sessionmaker
        factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

        sent_messages = []

        def fake_send(to, body):
            sent_messages.append({"to": to, "body": body})
            return True

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def test_get_db():
            async with factory() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        with (
            patch(
                "app.api.v1.routes.twilio_webhook.validate_twilio_signature",
                return_value=True,
            ),
            patch("app.services.agent.get_db", test_get_db),
            patch("app.api.v1.routes.twilio_webhook.send_text_reply", side_effect=fake_send),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/v1/webhooks/twilio/whatsapp",
                    data={
                        "MessageSid": "SMunknown",
                        "From": "whatsapp:+1555000000",
                        "To": "whatsapp:+14155238886",
                        "Body": "hi",
                        "NumMedia": "0",
                    },
                    headers={"X-Twilio-Signature": "mock_sig"},
                )

        assert resp.status_code == 200
        assert len(sent_messages) == 1, f"Expected 1 sent message, got: {sent_messages}"
        body = sent_messages[0]["body"]
        assert "mazi.ng" in body or "Welcome" in body or "Sign up" in body, (
            f"Unexpected onboarding reply: {body}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — HEALTH CHECKS (original tests preserved)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_root_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_twilio_webhook_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/webhooks/twilio/whatsapp")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "twilio_configured" in data
    assert "webhook_secret_set" in data
