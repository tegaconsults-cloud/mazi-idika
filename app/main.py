"""
Mazi Idika — FastAPI backend
Idika = market in Igbo
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings

app = FastAPI(
    title="Mazi Idika API",
    description="Backend for Mazi — Nigerian inventory & business management platform",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — tighten origins in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/", tags=["health"])
async def root():
    return {"status": "ok", "service": "mazi-idika", "version": "0.1.0"}


@app.get("/health", tags=["health"])
async def health():
    return {"status": "healthy"}
