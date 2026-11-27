"""Central v1 API router — mounts all route modules."""

from fastapi import APIRouter

from app.api.v1.routes import (
    auth,
    businesses,
    products,
    sales,
    debts,
    customers,
    reports,
    webhooks,         # Meta WhatsApp Cloud API + Paystack (unchanged)
    twilio_webhook,   # Twilio WhatsApp agent (new)
)

api_router = APIRouter()

api_router.include_router(auth.router,            prefix="/auth",       tags=["Auth"])
api_router.include_router(businesses.router,      prefix="/businesses", tags=["Businesses"])
api_router.include_router(products.router,        prefix="/products",   tags=["Products"])
api_router.include_router(sales.router,           prefix="/sales",      tags=["Sales"])
api_router.include_router(debts.router,           prefix="/debts",      tags=["Debts"])
api_router.include_router(customers.router,       prefix="/customers",  tags=["Customers"])
api_router.include_router(reports.router,         prefix="/reports",    tags=["Reports"])
api_router.include_router(webhooks.router,        prefix="/webhooks",   tags=["Webhooks"])
api_router.include_router(twilio_webhook.router,  prefix="/webhooks",   tags=["Webhooks"])
# Twilio endpoint is at: POST /api/v1/webhooks/twilio/whatsapp
