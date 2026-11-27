"""Reports and analytics routes."""

from fastapi import APIRouter, Query
from typing import Optional

router = APIRouter()


@router.get("/summary")
async def get_summary(
    branch_id: str = Query(...),
    period: str = Query("today"),  # "today" | "week" | "month" | "custom"
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    """Daily/weekly/monthly P&L summary. Used by dashboard KPIs and WhatsApp AI replies."""
    return {
        "period": period,
        "branch_id": branch_id,
        "revenue": 0,
        "cost_of_goods": 0,
        "gross_profit": 0,
        "expenses": 0,
        "net_profit": 0,
        "transactions_count": 0,
        "top_products": [],
        "slow_products": [],
    }


@router.get("/stock-valuation")
async def stock_valuation(branch_id: str = Query(...)):
    """Total stock value at cost and sell price."""
    return {
        "branch_id": branch_id,
        "total_cost_value": 0,
        "total_sell_value": 0,
        "low_stock_items": [],
        "out_of_stock_items": [],
    }


@router.get("/export")
async def export_report(
    branch_id: str = Query(...),
    period: str = Query("month"),
    format: str = Query("pdf"),  # "pdf" | "csv" | "excel"
):
    """Generate and return a downloadable report file.
    Returns a signed URL to the generated file in Supabase Storage.
    """
    # TODO: generate report → upload to storage → return signed URL
    return {
        "status": "not_implemented",
        "message": "Report export coming in Phase 2.",
        "format": format,
    }


@router.get("/analytics")
async def get_analytics(
    branch_id: str = Query(...),
    period: str = Query("month"),
):
    """Advanced analytics — payment method breakdown, basket size, trends."""
    return {
        "branch_id": branch_id,
        "payment_breakdown": {},
        "avg_basket_size": 0,
        "daily_trend": [],
    }
