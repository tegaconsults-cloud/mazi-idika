"""
Mazi Idika — FastAPI backend
Idika = market in Igbo
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Startup / shutdown lifecycle ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs once at startup before the server begins accepting requests.
    Validates that every required environment variable is set so a
    misconfigured Render deploy fails fast with a clear log message.
    """
    _validate_env()
    logger.info(
        f"Mazi Idika starting — env={settings.APP_ENV} "
        f"twilio={'OK' if settings.TWILIO_AUTH_TOKEN else 'MISSING'} "
        f"db={'OK' if settings.DATABASE_URL else 'MISSING'}"
    )
    yield
    # Shutdown — nothing to clean up currently


def _validate_env() -> None:
    """Log warnings for every missing required variable. Never crash on import."""
    required = {
        "DATABASE_URL":          settings.DATABASE_URL,
        "SECRET_KEY":            settings.SECRET_KEY,
        "TWILIO_ACCOUNT_SID":    settings.TWILIO_ACCOUNT_SID,
        "TWILIO_AUTH_TOKEN":     settings.TWILIO_AUTH_TOKEN,
        "TWILIO_WHATSAPP_NUMBER": settings.TWILIO_WHATSAPP_NUMBER,
    }
    missing = [k for k, v in required.items() if not v or v.startswith("change-me")]
    if missing:
        logger.warning(
            f"[startup] Missing or placeholder env vars: {', '.join(missing)}. "
            "Webhook requests will be rejected until these are set."
        )

    if settings.APP_ENV == "production" and settings.SECRET_KEY == "change-me-in-production":
        logger.error(
            "[startup] SECRET_KEY is still the placeholder value in production! "
            "Set a real random SECRET_KEY in Render environment variables."
        )


# ── App factory ───────────────────────────────────────────────────────────────

# Disable interactive docs in production — they expose the full API surface.
_docs_url    = None if settings.APP_ENV == "production" else "/docs"
_redoc_url   = None if settings.APP_ENV == "production" else "/redoc"
_openapi_url = None if settings.APP_ENV == "production" else "/openapi.json"

app = FastAPI(
    title="Mazi Idika API",
    description="Backend for Mazi — Nigerian inventory & business management platform",
    version="0.1.0",
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    openapi_url=_openapi_url,
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# In production, CORS_ORIGINS is set via the Render environment variable.
# Default includes localhost for local dev only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(api_router, prefix="/api/v1")


# ── Health endpoints ──────────────────────────────────────────────────────────

@app.get("/", tags=["health"])
async def root():
    return {"status": "ok", "service": "mazi-idika", "version": "0.1.0"}


@app.get("/health", tags=["health"])
async def health():
    return {"status": "healthy"}
