"""AI intent classifier for WhatsApp messages.

Classifies incoming text (English, Pidgin, Yoruba, Hausa, Igbo) into
structured intents that the backend can act on.

Phase 4 implementation — stub returns UNKNOWN until wired to LLM API.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional


class Intent(str, Enum):
    RECORD_SALE     = "record_sale"
    RESTOCK         = "restock"
    QUERY_DEBTS     = "query_debts"
    RECORD_DEBT     = "record_debt"
    SETTLE_DEBT     = "settle_debt"
    QUERY_SUMMARY   = "query_summary"
    QUERY_STOCK     = "query_stock"
    QUERY_TOP       = "query_top_products"
    ADD_EXPENSE     = "add_expense"
    UNKNOWN         = "unknown"


@dataclass
class ParsedIntent:
    intent: Intent
    confidence: float
    entities: dict          # extracted values e.g. {"product": "Indomie", "qty": 5, "amount": 3500}
    raw_text: str
    language_detected: str  # "en" | "pcm" (Pidgin) | "yo" | "ha" | "ig"


async def classify_intent(text: str) -> ParsedIntent:
    """
    Classify a natural language message into a structured intent.

    TODO (Phase 4):
    1. Send text to LLM (GPT-4o / Gemini) with Nigerian market context prompt
    2. Parse JSON response into ParsedIntent
    3. Fall back to keyword matching for common Pidgin phrases
    """
    # Keyword fallback — extend this before Phase 4 LLM integration
    text_lower = text.lower()

    if any(w in text_lower for w in ["sold", "sell", "ta", "ita"]):
        return ParsedIntent(Intent.RECORD_SALE, 0.6, {}, text, "en")

    if any(w in text_lower for w in ["restock", "add", "reach", "don reach", "fi wa"]):
        return ParsedIntent(Intent.RESTOCK, 0.6, {}, text, "pcm")

    if any(w in text_lower for w in ["owe", "debt", "credit", "who dey", "tani", "wa jẹ"]):
        return ParsedIntent(Intent.QUERY_DEBTS, 0.6, {}, text, "en")

    if any(w in text_lower for w in ["today", "summary", "report", "oni", "weekly", "monthly"]):
        return ParsedIntent(Intent.QUERY_SUMMARY, 0.6, {}, text, "en")

    if any(w in text_lower for w in ["stock", "how many", "melo", "nawa"]):
        return ParsedIntent(Intent.QUERY_STOCK, 0.6, {}, text, "en")

    return ParsedIntent(Intent.UNKNOWN, 0.0, {}, text, "en")
