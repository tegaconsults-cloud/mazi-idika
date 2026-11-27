"""Application configuration — reads from environment variables."""

from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # ── App ──────────────────────────────────────────────────────────────────
    APP_ENV: str = "development"
    SECRET_KEY: str = "change-me-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # ── Database ─────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/mazi"

    # ── Supabase ─────────────────────────────────────────────────────────────
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""

    # ── CORS ─────────────────────────────────────────────────────────────────
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "https://mazi.ng",
    ]

    # ── Paystack ─────────────────────────────────────────────────────────────
    PAYSTACK_SECRET_KEY: str = ""
    PAYSTACK_PUBLIC_KEY: str = ""

    # ── Meta WhatsApp Cloud API (preserved, unchanged) ───────────────────────
    WHATSAPP_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_VERIFY_TOKEN: str = ""

    # ── Twilio WhatsApp ───────────────────────────────────────────────────────
    # From https://console.twilio.com → Account Info
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    # The Twilio WhatsApp sandbox / approved sender number, e.g. "+14155238886"
    TWILIO_WHATSAPP_NUMBER: str = ""
    # Shared secret appended to the webhook URL as ?token=<this> for extra protection
    # Can be any random string you set in Render env vars
    TWILIO_WEBHOOK_SECRET: str = ""

    # ── AI / LLM ─────────────────────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    GEMINI_API_KEY: str = ""  # Primary LLM for Nigerian-language understanding

    # ── Voice (speech-to-text) ───────────────────────────────────────────────
    DEEPGRAM_API_KEY: str = ""  # Used for voice note transcription

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
