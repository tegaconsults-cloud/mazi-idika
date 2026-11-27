"""LLM intent + entity extraction service.

Primary: Google Gemini (gemini-1.5-flash — fast, cheap, good at Nigerian English/Pidgin)
Fallback: keyword matching (always works, even without API key)

The LLM is given a system prompt that:
- Understands Nigerian Pidgin, Yoruba, Hausa, Igbo market vocabulary
- Returns structured JSON for every recognised intent
- Knows Mazi's data model (products, sales, debts, customers, reports)
"""

import json
import logging
import re
from typing import Optional

from app.services.ai_intent import Intent, ParsedIntent, classify_intent as keyword_classify

logger = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Mazi, an intelligent business assistant for Nigerian market traders.
You help traders manage inventory, record sales, track debts, generate reports, and more.

Understand messages in English, Nigerian Pidgin, Yoruba, Hausa, and Igbo.
Common Pidgin phrases:
- "don reach" / "don come" = stock arrived / restock
- "sell am" / "ta" = sold
- "who dey owe me" / "my debtors" = query debts
- "settle am" = mark debt as paid
- "how much e remain" / "stock dey?" = query stock level
- "summary" / "wetin i make today" = daily report
- "top" = top selling products
- "slow" = slow moving products
- "add am" / "fi wa" = add product/restock
- "k" / "K" = thousands (e.g. "5k" = 5000)

Extract the intent and entities. Reply ONLY with valid JSON in this exact format:
{
  "intent": "<one of the intents below>",
  "confidence": <0.0-1.0>,
  "language": "<en|pcm|yo|ha|ig>",
  "entities": {
    "product_name": "<name or null>",
    "quantity": <number or null>,
    "unit": "<carton|bag|tin|piece|unit or null>",
    "amount": <naira amount as number or null>,
    "cost_price": <naira or null>,
    "sell_price": <naira or null>,
    "customer_name": "<name or null>",
    "payment_method": "<cash|transfer|pos|credit or null>",
    "period": "<today|week|month|custom or null>",
    "supplier_name": "<name or null>"
  },
  "reply_hint": "<a one-line friendly reply in same language as the message>"
}

Valid intents:
- record_sale        : trader sold something
- restock            : stock arrived or trader adding inventory
- query_stock        : asking about current stock level
- query_debts        : asking who owes money
- record_debt        : recording a new credit sale
- settle_debt        : customer paid back a debt
- query_summary      : asking for P&L / daily / weekly / monthly report
- query_top          : top selling products
- query_slow         : slow moving products
- add_expense        : recording a business expense
- add_customer       : registering a new customer
- add_product        : adding a new product to inventory
- delete_product     : removing a product
- send_reminder      : send debt reminders to customers
- help               : user asking what Mazi can do
- greeting           : just saying hi / hello
- unknown            : cannot determine intent

Important rules:
- "k" always means × 1000 (e.g. "2.5k" = 2500, "10k" = 10000)
- If a quantity word like "carton", "bag", "crate" appears, capture it in "unit"
- If the message mentions a name + "credit" / "owe" / "jara" / "borrow", that is record_debt
- Do not guess — if unsure, use "unknown"
"""

# ── Gemini call ───────────────────────────────────────────────────────────────

async def _call_gemini(text: str) -> Optional[dict]:
    """Call Gemini 1.5 Flash and return parsed JSON, or None on failure."""
    try:
        import google.generativeai as genai
        from app.core.config import settings

        if not settings.GEMINI_API_KEY:
            return None

        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config={"response_mime_type": "application/json"},
        )
        prompt = f"{SYSTEM_PROMPT}\n\nMessage: {text}"
        response = model.generate_content(prompt)
        raw = response.text.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)

        return json.loads(raw)
    except Exception as exc:
        logger.warning(f"Gemini call failed: {exc}")
        return None


# ── OpenAI fallback ───────────────────────────────────────────────────────────

async def _call_openai(text: str) -> Optional[dict]:
    """Call OpenAI GPT-4o-mini as secondary fallback."""
    try:
        import openai
        from app.core.config import settings

        if not settings.OPENAI_API_KEY:
            return None

        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": text},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=400,
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as exc:
        logger.warning(f"OpenAI call failed: {exc}")
        return None


# ── Public entry point ────────────────────────────────────────────────────────

async def classify_message(text: str) -> ParsedIntent:
    """
    Classify a WhatsApp message into a structured ParsedIntent.

    Priority:
    1. Gemini 1.5 Flash (best Nigerian language understanding)
    2. OpenAI GPT-4o-mini
    3. Keyword fallback (always works)

    Returns a ParsedIntent with entities populated where possible.
    """
    if not text or not text.strip():
        return ParsedIntent(Intent.UNKNOWN, 0.0, {}, text or "", "en")

    # Try LLMs
    result = await _call_gemini(text) or await _call_openai(text)

    if result:
        try:
            intent_str = result.get("intent", "unknown")
            # Map to enum, default to UNKNOWN
            try:
                intent = Intent(intent_str)
            except ValueError:
                intent = Intent.UNKNOWN

            entities = result.get("entities", {})
            # Clean None values
            entities = {k: v for k, v in entities.items() if v is not None}

            return ParsedIntent(
                intent=intent,
                confidence=float(result.get("confidence", 0.7)),
                entities=entities,
                raw_text=text,
                language_detected=result.get("language", "en"),
            )
        except Exception as exc:
            logger.warning(f"Failed to parse LLM result: {exc} — {result}")

    # Keyword fallback
    return await keyword_classify(text)


def format_naira(amount: float) -> str:
    """Format a number as Nigerian Naira string."""
    if amount >= 1_000_000:
        return f"₦{amount/1_000_000:.1f}M"
    if amount >= 1_000:
        return f"₦{amount:,.0f}"
    return f"₦{amount:.0f}"
