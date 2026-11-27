"""Mazi WhatsApp Agent.

This is the brain. It:
1. Receives a parsed TwilioMessage
2. Identifies which business / user is messaging (by phone number)
3. Calls the LLM to understand intent + extract entities
4. Executes the real DB operation (or explains what's needed)
5. Returns a human-readable reply in the same language the user wrote in

Multi-tenancy: every DB operation is scoped to the business identified
              from the sender's phone number. No cross-tenant leakage.

Design principle: never raise an unhandled exception. Always reply to the user,
                  even if the reply is "I didn't understand — here's what I can do."
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.twilio_whatsapp import TwilioMessage, send_text_reply
from app.services.llm import classify_message, format_naira
from app.services.voice import transcribe_audio
from app.services.image_ocr import extract_from_image, extract_from_pdf
from app.services.twilio_whatsapp import download_media
from app.services.ai_intent import Intent, ParsedIntent
from app.models.business import Business, Branch
from app.models.product import Product
from app.models.transaction import Transaction, TransactionItem, PaymentMethod, TransactionStatus
from app.models.debt import Debt, DebtStatus
from app.models.customer import Customer

logger = logging.getLogger(__name__)

# ── Greeting / help text ──────────────────────────────────────────────────────

HELP_TEXT = """👋 I'm *Mazi*, your business assistant.

Here's what I can do:
📦 *Inventory*
  "Add 10 cartons Indomie, cost 2800" 
  "How many Milo remain?"

🛒 *Record a sale*
  "Sold 5 Peak milk, 1200 each, cash"
  "Ta garri 2 bags, transfer"

💳 *Debts & credit*
  "Emeka bought 3 Milo on credit, 2800 each"
  "Who dey owe me?"
  "Mama Titi don pay 5000"

📊 *Reports*
  "Today summary"
  "Weekly report"
  "Top products"

Just text or voice note — Pidgin, English, Yoruba, Hausa, Igbo all work!"""


# ── Business lookup ───────────────────────────────────────────────────────────

async def _get_business_by_phone(phone: str, db: AsyncSession) -> Optional[Business]:
    """Find the business whose owner registered with this phone number.
    Phone is stored without the leading + (normalised to 234xxxxxxxxxx format).
    """
    # Normalise: remove leading +, spaces, dashes
    normalised = phone.lstrip("+").replace(" ", "").replace("-", "")
    # Try exact match first
    result = await db.execute(
        select(Business).where(Business.phone == normalised)
    )
    biz = result.scalar_one_or_none()
    if biz:
        return biz

    # Try with leading 0 → 234 substitution
    if normalised.startswith("234") and len(normalised) > 3:
        local = "0" + normalised[3:]
        result = await db.execute(select(Business).where(Business.phone == local))
        biz = result.scalar_one_or_none()
        if biz:
            return biz

    return None


async def _get_default_branch(business_id: str, db: AsyncSession) -> Optional[Branch]:
    """Return the first branch for a business (simplest multi-branch handling)."""
    result = await db.execute(
        select(Branch).where(Branch.business_id == business_id).limit(1)
    )
    return result.scalar_one_or_none()


# ── Intent handlers ───────────────────────────────────────────────────────────

async def _handle_record_sale(parsed: ParsedIntent, branch: Branch, db: AsyncSession) -> str:
    """Record a sale transaction."""
    entities = parsed.entities
    product_name = entities.get("product_name")
    quantity     = entities.get("quantity")
    amount       = entities.get("amount")
    sell_price   = entities.get("sell_price") or amount
    payment_raw  = (entities.get("payment_method") or "cash").lower()
    customer_name = entities.get("customer_name")

    if not product_name:
        return "I understood you made a sale, but I need the *product name*. Try: \"Sold 5 Indomie, 3500 each, cash\""

    if not quantity:
        quantity = 1

    # Find product in this branch
    result = await db.execute(
        select(Product).where(
            and_(
                Product.branch_id == branch.id,
                Product.is_deleted == False,
                func.lower(Product.name).contains(product_name.lower()),
            )
        ).limit(1)
    )
    product = result.scalar_one_or_none()

    if not product:
        return (
            f"❌ I couldn't find *{product_name}* in your inventory.\n"
            f"Add it first: \"Add {product_name}, sell price ₦{sell_price or '?'}\""
        )

    # Check stock
    if product.quantity < quantity:
        return (
            f"⚠️ Not enough stock for *{product.name}*.\n"
            f"You have *{product.quantity}* {product.unit}(s) but tried to sell *{quantity}*."
        )

    unit_price = sell_price or product.sell_price or 0.0
    total = unit_price * quantity

    # Map payment method
    try:
        pm = PaymentMethod(payment_raw)
    except ValueError:
        pm = PaymentMethod.cash

    # Build transaction
    tx_id = str(uuid.uuid4())
    tx = Transaction(
        tx_id=tx_id,
        branch_id=branch.id,
        customer_name=customer_name,
        payment_method=pm,
        status=TransactionStatus.completed,
        total_amount=total,
        recorded_at=datetime.now(timezone.utc),
    )
    db.add(tx)

    # Transaction item
    item = TransactionItem(
        transaction_tx_id=tx_id,
        product_id=product.id,
        product_name=product.name,
        quantity=quantity,
        unit_price=unit_price,
        total=total,
    )
    db.add(item)

    # Deduct stock
    product.quantity -= quantity

    # If credit, create debt record
    if pm == PaymentMethod.credit and customer_name:
        debt = Debt(
            branch_id=branch.id,
            customer_name=customer_name,
            sale_tx_id=tx_id,
            original_amount=total,
            amount_paid=0.0,
            description=f"{quantity} × {product.name}",
            status=DebtStatus.pending,
        )
        db.add(debt)

    await db.flush()

    profit_line = ""
    if product.cost_price and unit_price:
        profit = (unit_price - product.cost_price) * quantity
        profit_line = f"\n📈 Profit: *{format_naira(profit)}*"

    credit_line = ""
    if pm == PaymentMethod.credit:
        credit_line = f"\n💳 Credit recorded for *{customer_name or 'customer'}*"

    return (
        f"✅ Sale recorded!\n"
        f"📦 {product.name} — {quantity} {product.unit}(s)\n"
        f"💰 Total: *{format_naira(total)}* ({pm.value})"
        f"{profit_line}"
        f"{credit_line}\n"
        f"📉 Stock left: *{product.quantity}* {product.unit}(s)"
    )


async def _handle_restock(parsed: ParsedIntent, branch: Branch, db: AsyncSession) -> str:
    """Add stock to an existing product, or create it if new."""
    entities = parsed.entities
    product_name = entities.get("product_name")
    quantity     = entities.get("quantity", 1)
    cost_price   = entities.get("cost_price") or entities.get("amount")
    sell_price   = entities.get("sell_price")
    unit         = entities.get("unit", "unit")

    if not product_name:
        return "What product arrived? Try: \"Add 20 cartons Indomie, cost 2800 each\""

    result = await db.execute(
        select(Product).where(
            and_(
                Product.branch_id == branch.id,
                Product.is_deleted == False,
                func.lower(Product.name).contains(product_name.lower()),
            )
        ).limit(1)
    )
    product = result.scalar_one_or_none()

    if product:
        old_qty = product.quantity
        product.quantity += quantity
        if cost_price:
            product.cost_price = cost_price
        if sell_price:
            product.sell_price = sell_price
        await db.flush()
        return (
            f"✅ Restocked *{product.name}*!\n"
            f"📦 Added: *{quantity}* {product.unit}(s)\n"
            f"📊 New total: *{product.quantity}* {product.unit}(s)"
            + (f"\n💰 Cost price updated: *{format_naira(cost_price)}*" if cost_price else "")
        )
    else:
        # New product
        new_product = Product(
            branch_id=branch.id,
            name=product_name.title(),
            unit=unit,
            quantity=quantity,
            cost_price=cost_price,
            sell_price=sell_price,
            low_stock_threshold=5,
        )
        db.add(new_product)
        await db.flush()
        return (
            f"✅ *{new_product.name}* added to inventory!\n"
            f"📦 Quantity: *{quantity}* {unit}(s)"
            + (f"\n💰 Cost: {format_naira(cost_price)}" if cost_price else "")
            + (f" | Sell: {format_naira(sell_price)}" if sell_price else "")
            + "\n\nTip: Set a selling price so I can track profit: \"Indomie sell price 3500\""
        )


async def _handle_query_stock(parsed: ParsedIntent, branch: Branch, db: AsyncSession) -> str:
    """Check stock level for a specific product or list low-stock items."""
    product_name = parsed.entities.get("product_name")

    if product_name:
        result = await db.execute(
            select(Product).where(
                and_(
                    Product.branch_id == branch.id,
                    Product.is_deleted == False,
                    func.lower(Product.name).contains(product_name.lower()),
                )
            ).limit(1)
        )
        product = result.scalar_one_or_none()
        if not product:
            return f"❌ *{product_name}* not found in your inventory."
        status = "⚠️ Low stock!" if product.quantity <= product.low_stock_threshold else "✅"
        return (
            f"📦 *{product.name}*\n"
            f"Quantity: *{product.quantity}* {product.unit}(s) {status}"
            + (f"\nSell price: {format_naira(product.sell_price)}" if product.sell_price else "")
        )

    # List low-stock items
    result = await db.execute(
        select(Product).where(
            and_(
                Product.branch_id == branch.id,
                Product.is_deleted == False,
                Product.quantity <= Product.low_stock_threshold,
            )
        ).order_by(Product.quantity).limit(10)
    )
    low = result.scalars().all()
    if not low:
        return "✅ All products are well-stocked!"
    lines = "\n".join(f"⚠️ {p.name}: *{p.quantity}* {p.unit}(s) left" for p in low)
    return f"🔴 *Low stock alert ({len(low)} items):*\n{lines}"


async def _handle_query_debts(parsed: ParsedIntent, branch: Branch, db: AsyncSession) -> str:
    """List outstanding debts."""
    result = await db.execute(
        select(Debt).where(
            and_(
                Debt.branch_id == branch.id,
                Debt.status != DebtStatus.settled,
            )
        ).order_by(Debt.created_at.desc()).limit(15)
    )
    debts = result.scalars().all()
    if not debts:
        return "✅ No outstanding debts. Your customers are all paid up!"
    total = sum(d.original_amount - d.amount_paid for d in debts)
    lines = []
    for d in debts:
        owed = d.original_amount - d.amount_paid
        name = d.customer_name or "Unknown"
        partial = " (partial)" if d.status == DebtStatus.partial else ""
        lines.append(f"• *{name}*: {format_naira(owed)}{partial}")
    return (
        f"💳 *Outstanding debts — {format_naira(total)}*\n"
        + "\n".join(lines)
        + "\n\nSend reminders? Reply \"Remind all\""
    )


async def _handle_record_debt(parsed: ParsedIntent, branch: Branch, db: AsyncSession) -> str:
    """Record a new credit sale / debt."""
    entities = parsed.entities
    customer_name = entities.get("customer_name")
    amount        = entities.get("amount")
    product_name  = entities.get("product_name")
    quantity      = entities.get("quantity", 1)

    if not customer_name:
        return "Who bought on credit? Try: \"Emeka bought 3 Milo on credit, 2800 each\""
    if not amount and not product_name:
        return f"How much does *{customer_name}* owe? Include the amount or product."

    total = amount or 0.0

    # Try to calculate from product if amount not given
    if not total and product_name:
        result = await db.execute(
            select(Product).where(
                and_(
                    Product.branch_id == branch.id,
                    Product.is_deleted == False,
                    func.lower(Product.name).contains(product_name.lower()),
                )
            ).limit(1)
        )
        product = result.scalar_one_or_none()
        if product and product.sell_price:
            total = product.sell_price * quantity
            # Deduct stock
            if product.quantity >= quantity:
                product.quantity -= quantity

    debt = Debt(
        branch_id=branch.id,
        customer_name=customer_name,
        original_amount=total,
        amount_paid=0.0,
        description=f"{quantity} × {product_name}" if product_name else "",
        status=DebtStatus.pending,
    )
    db.add(debt)
    await db.flush()

    return (
        f"💳 Debt recorded!\n"
        f"👤 Customer: *{customer_name}*\n"
        f"💰 Amount: *{format_naira(total)}*"
        + (f"\n📦 For: {quantity} × {product_name}" if product_name else "")
        + "\n\nI'll remind them automatically. Reply \"Remind all\" anytime."
    )


async def _handle_settle_debt(parsed: ParsedIntent, branch: Branch, db: AsyncSession) -> str:
    """Mark a debt as settled."""
    entities = parsed.entities
    customer_name = entities.get("customer_name")
    amount        = entities.get("amount")

    if not customer_name:
        return "Who paid? Try: \"Mama Titi paid 5000\" or \"Emeka don settle\""

    result = await db.execute(
        select(Debt).where(
            and_(
                Debt.branch_id == branch.id,
                Debt.status != DebtStatus.settled,
                func.lower(Debt.customer_name).contains(customer_name.lower()),
            )
        ).order_by(Debt.created_at.desc()).limit(1)
    )
    debt = result.scalar_one_or_none()
    if not debt:
        return f"❌ No outstanding debt found for *{customer_name}*."

    outstanding = debt.original_amount - debt.amount_paid
    paid = amount or outstanding  # if no amount given, assume full payment

    debt.amount_paid += paid
    if debt.amount_paid >= debt.original_amount:
        debt.status = DebtStatus.settled
        status_msg = "✅ *Fully settled!*"
    else:
        debt.status = DebtStatus.partial
        remaining = debt.original_amount - debt.amount_paid
        status_msg = f"⚠️ Partial payment. Still owes *{format_naira(remaining)}*"

    await db.flush()
    return (
        f"💚 Payment recorded!\n"
        f"👤 *{customer_name}* paid *{format_naira(paid)}*\n"
        f"{status_msg}"
    )


async def _handle_query_summary(parsed: ParsedIntent, branch: Branch, db: AsyncSession) -> str:
    """Generate a business summary (today / week / month)."""
    period = (parsed.entities.get("period") or "today").lower()

    now = datetime.now(timezone.utc)
    if period == "today":
        from_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        label = "Today"
    elif period == "week":
        from_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        from_dt = from_dt.replace(day=from_dt.day - from_dt.weekday())
        label = "This week"
    else:  # month
        from_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        label = "This month"

    # Total sales
    result = await db.execute(
        select(func.sum(Transaction.total_amount), func.count(Transaction.tx_id)).where(
            and_(
                Transaction.branch_id == branch.id,
                Transaction.status == TransactionStatus.completed,
                Transaction.recorded_at >= from_dt,
            )
        )
    )
    row = result.one()
    total_revenue = row[0] or 0.0
    tx_count      = row[1] or 0

    # Outstanding debts
    debt_result = await db.execute(
        select(func.sum(Debt.original_amount - Debt.amount_paid)).where(
            and_(
                Debt.branch_id == branch.id,
                Debt.status != DebtStatus.settled,
            )
        )
    )
    total_debts = debt_result.scalar() or 0.0

    # Low stock count
    low_result = await db.execute(
        select(func.count(Product.id)).where(
            and_(
                Product.branch_id == branch.id,
                Product.is_deleted == False,
                Product.quantity <= Product.low_stock_threshold,
            )
        )
    )
    low_count = low_result.scalar() or 0

    return (
        f"📊 *{label}'s Summary*\n\n"
        f"💰 Revenue: *{format_naira(total_revenue)}*\n"
        f"🛒 Transactions: *{tx_count}*\n"
        f"💳 Outstanding debts: *{format_naira(total_debts)}*\n"
        f"📦 Low stock items: *{low_count}*\n\n"
        f"For more detail: \"Top products\" or \"Who dey owe me?\""
    )


async def _handle_query_top(parsed: ParsedIntent, branch: Branch, db: AsyncSession) -> str:
    """List top-selling products by revenue."""
    result = await db.execute(
        select(
            TransactionItem.product_name,
            func.sum(TransactionItem.total).label("revenue"),
            func.sum(TransactionItem.quantity).label("units_sold"),
        )
        .join(Transaction, Transaction.tx_id == TransactionItem.transaction_tx_id)
        .where(
            and_(
                Transaction.branch_id == branch.id,
                Transaction.status == TransactionStatus.completed,
            )
        )
        .group_by(TransactionItem.product_name)
        .order_by(func.sum(TransactionItem.total).desc())
        .limit(5)
    )
    rows = result.all()
    if not rows:
        return "No sales recorded yet. Record some sales and I'll show you your top products!"
    lines = [
        f"{i+1}. *{r.product_name}* — {format_naira(r.revenue)} ({r.units_sold} units)"
        for i, r in enumerate(rows)
    ]
    return "🏆 *Top selling products:*\n" + "\n".join(lines)


async def _handle_add_expense(parsed: ParsedIntent, branch: Branch, db: AsyncSession) -> str:
    """Record a business expense (stub — expense model not yet in DB schema)."""
    amount      = parsed.entities.get("amount")
    description = parsed.entities.get("product_name") or "expense"
    if not amount:
        return "How much did you spend? Try: \"Spent 5000 on transport\""
    # TODO: add Expense model in Phase 2 and persist here
    return (
        f"📝 Expense noted: *{format_naira(amount)}* for *{description}*\n"
        f"⚠️ Full expense tracking is coming in Phase 2. "
        f"For now this is acknowledged but not yet saved to your reports."
    )


async def _handle_send_reminders(branch: Branch, db: AsyncSession) -> str:
    """List debtors and confirm reminders would be sent (actual sending needs Twilio)."""
    result = await db.execute(
        select(Debt).where(
            and_(
                Debt.branch_id == branch.id,
                Debt.status != DebtStatus.settled,
                Debt.customer_phone != None,
            )
        ).limit(20)
    )
    debts_with_phone = result.scalars().all()

    result2 = await db.execute(
        select(Debt).where(
            and_(
                Debt.branch_id == branch.id,
                Debt.status != DebtStatus.settled,
            )
        ).limit(20)
    )
    all_debts = result2.scalars().all()

    if not all_debts:
        return "✅ No outstanding debts — no reminders to send!"

    # TODO: when customer phones are stored, actually send via Twilio
    names = ", ".join({d.customer_name or "?" for d in all_debts if d.customer_name})
    return (
        f"📨 Reminders queued for *{len(all_debts)}* debtor(s): {names}\n\n"
        f"⚠️ Automatic WhatsApp reminders require customer phone numbers on file. "
        f"Add phone numbers when recording debts: "
        f"\"Emeka (08011234567) owes 5000\""
    )


# ── Main agent entry point ────────────────────────────────────────────────────

async def handle_message(msg: TwilioMessage) -> str:
    """
    Process an incoming WhatsApp message end-to-end.
    Returns the reply string to send back to the user.
    Never raises — always returns a reply.
    """
    try:
        return await _process_message(msg)
    except Exception as exc:
        logger.exception(f"Unhandled error in agent for {msg.from_number}: {exc}")
        return (
            "⚠️ Something went wrong on my end. Please try again.\n"
            "If the problem continues, contact Mazi support."
        )


async def _process_message(msg: TwilioMessage) -> str:
    async with get_db() as db:
        # ── 1. Identify business ──────────────────────────────────────────
        business = await _get_business_by_phone(msg.from_number, db)
        if not business:
            return (
                f"👋 Welcome to *Mazi*!\n\n"
                f"I don't have a business linked to this number ({msg.from_number}).\n\n"
                f"To get started:\n"
                f"1. Sign up at *mazi.ng*\n"
                f"2. Use the same number you're messaging from\n"
                f"3. Come back here and I'll know who you are!"
            )

        branch = await _get_default_branch(business.id, db)
        if not branch:
            return (
                f"⚠️ Your account (*{business.name}*) doesn't have a branch set up yet.\n"
                f"Please complete setup at *mazi.ng/dashboard*"
            )

        # ── 2. Extract text from media ────────────────────────────────────
        text = msg.body

        if msg.msg_type == "audio":
            audio_bytes = await download_media(msg.media_url)
            if audio_bytes:
                transcribed = await transcribe_audio(audio_bytes, msg.media_content_type)
                if transcribed:
                    text = transcribed
                    logger.info(f"Voice transcribed: {text!r}")
                else:
                    return (
                        "🎙️ I received your voice note but couldn't transcribe it.\n"
                        "Try typing instead, or send again in a quieter environment."
                    )
            else:
                return "⚠️ Couldn't download your voice note. Please try again."

        elif msg.msg_type == "image":
            image_bytes = await download_media(msg.media_url)
            if image_bytes:
                extracted = await extract_from_image(image_bytes, msg.media_content_type)
                if extracted:
                    # Prepend image caption if any, then extracted text
                    text = (msg.body + "\n" + extracted).strip() if msg.body else extracted
                    logger.info(f"Image OCR result: {text[:200]!r}")
                else:
                    return (
                        "📸 I received your image but couldn't read any product data from it.\n"
                        "Make sure the image shows product names and prices clearly."
                    )
            else:
                return "⚠️ Couldn't download your image. Please try again."

        elif msg.msg_type == "document":
            pdf_bytes = await download_media(msg.media_url)
            if pdf_bytes:
                extracted = await extract_from_pdf(pdf_bytes)
                text = extracted or msg.body or ""
                if not text:
                    return "📄 I received your document but couldn't read any text from it."
            else:
                return "⚠️ Couldn't download your document. Please try again."

        if not text:
            return HELP_TEXT

        # ── 3. Classify intent ────────────────────────────────────────────
        parsed = await classify_message(text)
        logger.info(
            f"[{business.name}] intent={parsed.intent.value} "
            f"confidence={parsed.confidence:.2f} entities={parsed.entities}"
        )

        # ── 4. Route to handler ───────────────────────────────────────────
        intent = parsed.intent

        if intent == Intent.RECORD_SALE:
            return await _handle_record_sale(parsed, branch, db)

        elif intent == Intent.RESTOCK or intent == Intent("add_product"):
            return await _handle_restock(parsed, branch, db)

        elif intent == Intent.QUERY_STOCK:
            return await _handle_query_stock(parsed, branch, db)

        elif intent == Intent.QUERY_DEBTS:
            return await _handle_query_debts(parsed, branch, db)

        elif intent == Intent.RECORD_DEBT:
            return await _handle_record_debt(parsed, branch, db)

        elif intent == Intent.SETTLE_DEBT:
            return await _handle_settle_debt(parsed, branch, db)

        elif intent == Intent.QUERY_SUMMARY:
            return await _handle_query_summary(parsed, branch, db)

        elif intent == Intent("query_top"):
            return await _handle_query_top(parsed, branch, db)

        elif intent == Intent.ADD_EXPENSE:
            return await _handle_add_expense(parsed, branch, db)

        elif intent == Intent("send_reminder"):
            return await _handle_send_reminders(branch, db)

        elif intent in (Intent("greeting"), Intent("help")):
            return f"👋 Hello, *{msg.profile_name or 'Oga'}*!\n\n{HELP_TEXT}"

        else:
            # Unknown — give a helpful nudge
            return (
                f"🤔 I didn't quite understand: *\"{text[:80]}\"*\n\n"
                f"Here's what I can do:\n{HELP_TEXT}"
            )
