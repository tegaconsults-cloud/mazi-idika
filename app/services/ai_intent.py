"""AI intent classifier — keyword fallback.

This is the last-resort classifier used when both Gemini and OpenAI are
unavailable. The LLM-powered version is in app/services/llm.py.

Intents are extended to cover all agent capabilities.
"""

from enum import Enum
from dataclasses import dataclass, field


class Intent(str, Enum):
    # Sales
    RECORD_SALE    = "record_sale"
    # Inventory
    RESTOCK        = "restock"
    ADD_PRODUCT    = "add_product"
    DELETE_PRODUCT = "delete_product"
    QUERY_STOCK    = "query_stock"
    QUERY_TOP      = "query_top"
    QUERY_SLOW     = "query_slow"
    # Debts
    RECORD_DEBT    = "record_debt"
    SETTLE_DEBT    = "settle_debt"
    QUERY_DEBTS    = "query_debts"
    SEND_REMINDER  = "send_reminder"
    # Reports
    QUERY_SUMMARY  = "query_summary"
    ADD_EXPENSE    = "add_expense"
    # Customers / suppliers
    ADD_CUSTOMER   = "add_customer"
    # UI
    HELP           = "help"
    GREETING       = "greeting"
    UNKNOWN        = "unknown"


@dataclass
class ParsedIntent:
    intent: Intent
    confidence: float
    entities: dict = field(default_factory=dict)
    raw_text: str = ""
    language_detected: str = "en"


def _word_in(text: str, word: str) -> bool:
    """
    Check whether a keyword appears in text as a standalone token.
    Handles both space-padded matches and start/end-of-string anchoring
    so short Pidgin verbs like "ta" are caught reliably.
    """
    # Pad both sides so boundary checks work uniformly
    padded = " " + text + " "
    return (" " + word + " ") in padded


async def classify_intent(text: str) -> ParsedIntent:
    """
    Keyword-based intent classifier.
    Used as fallback when LLM APIs are unavailable.
    Not a replacement for the LLM — entity extraction is minimal here.
    """
    t = text.lower().strip()

    # ── Greetings ─────────────────────────────────────────────────────────────
    greeting_words = ["hi", "hello", "good morning", "good afternoon", "how far", "hey"]
    if any(_word_in(t, w) or t.startswith(w) for w in greeting_words):
        return ParsedIntent(Intent.GREETING, 0.9, {}, text, "en")

    # ── Help ──────────────────────────────────────────────────────────────────
    if any(w in t for w in ["help", "what can you", "how to use", "commands"]):
        return ParsedIntent(Intent.HELP, 0.9, {}, text, "en")

    # ── Sales ─────────────────────────────────────────────────────────────────
    # "ta" is Yoruba/Pidgin for "sell/sold" — must be a standalone word
    sale_words = ["sold", "sell", "i sell", "just sell", "customer buy", "ita "]
    if any(w in t for w in sale_words) or _word_in(t, "ta"):
        return ParsedIntent(Intent.RECORD_SALE, 0.6, {}, text, "en")

    # ── Restock / add inventory ───────────────────────────────────────────────
    restock_words = [
        "restock", "don reach", "don come", "just reach", "add stock",
        "new stock", "fi wa", "stock arrive", "received", "got delivery",
    ]
    if any(w in t for w in restock_words):
        return ParsedIntent(Intent.RESTOCK, 0.65, {}, text, "pcm")

    # ── Debt queries ──────────────────────────────────────────────────────────
    debt_query_words = [
        "who dey owe", "who owes", "debtors", "my debtors",
        "outstanding", "wa jẹ", "tani o je wa",
    ]
    if any(w in t for w in debt_query_words):
        return ParsedIntent(Intent.QUERY_DEBTS, 0.7, {}, text, "en")

    # ── Record debt / credit sale ─────────────────────────────────────────────
    record_debt_words = [
        "on credit", "credit sale", "jara", "owe me", "borrow",
        "buy on credit", "ya credit",
    ]
    if any(w in t for w in record_debt_words):
        return ParsedIntent(Intent.RECORD_DEBT, 0.65, {}, text, "en")

    # ── Settle debt ───────────────────────────────────────────────────────────
    settle_words = [
        "paid", "don pay", "settled", "pay back", "clear debt",
        "settle am", "don settle",
    ]
    if any(w in t for w in settle_words):
        return ParsedIntent(Intent.SETTLE_DEBT, 0.65, {}, text, "pcm")

    # ── Send reminders ────────────────────────────────────────────────────────
    if any(w in t for w in ["remind", "send reminder", "message them", "tell them"]):
        return ParsedIntent(Intent.SEND_REMINDER, 0.7, {}, text, "en")

    # ── Reports / summary ─────────────────────────────────────────────────────
    summary_words = [
        "today", "summary", "report", "wetin i make", "how much i make",
        "weekly", "monthly", "this week", "this month", "profit", "loss",
    ]
    if any(w in t for w in summary_words):
        period = "today"
        if "week" in t:
            period = "week"
        elif "month" in t:
            period = "month"
        return ParsedIntent(Intent.QUERY_SUMMARY, 0.7, {"period": period}, text, "en")

    # ── Top / slow products ───────────────────────────────────────────────────
    if any(w in t for w in ["top product", "best seller", "top selling", "bestsell"]):
        return ParsedIntent(Intent.QUERY_TOP, 0.75, {}, text, "en")
    if any(w in t for w in ["slow mover", "not selling", "dead stock"]):
        return ParsedIntent(Intent.QUERY_SLOW, 0.75, {}, text, "en")

    # ── Stock query ───────────────────────────────────────────────────────────
    stock_words = [
        "how many", "how much remain", "stock level", "how much dey",
        "quantity", "melo", "how much i get",
    ]
    if any(w in t for w in stock_words):
        return ParsedIntent(Intent.QUERY_STOCK, 0.65, {}, text, "en")

    # ── Expense ───────────────────────────────────────────────────────────────
    if any(w in t for w in ["spent", "expense", "pay for", "cost me", "i buy"]):
        return ParsedIntent(Intent.ADD_EXPENSE, 0.6, {}, text, "en")

    return ParsedIntent(Intent.UNKNOWN, 0.0, {}, text, "en")
