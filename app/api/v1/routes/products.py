"""Product / inventory routes."""

from fastapi import APIRouter, Query, status
from pydantic import BaseModel
from typing import Optional
import uuid

router = APIRouter()


class ProductCreate(BaseModel):
    name: str
    category: Optional[str] = None
    cost_price: Optional[float] = None
    sell_price: Optional[float] = None
    quantity: int = 0
    low_stock_threshold: int = 5
    unit: Optional[str] = "unit"  # e.g. "carton", "bag", "tin"
    branch_id: str


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    cost_price: Optional[float] = None
    sell_price: Optional[float] = None
    quantity: Optional[int] = None
    low_stock_threshold: Optional[int] = None


class StockAdjust(BaseModel):
    quantity_delta: int   # positive = restock, negative = manual deduction
    reason: Optional[str] = None  # e.g. "restock", "damage", "correction"


@router.get("/")
async def list_products(
    branch_id: str = Query(...),
    category: Optional[str] = Query(None),
    low_stock_only: bool = Query(False),
    search: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    """List products for a branch with optional filters."""
    # TODO: query products table filtered by branch_id (RLS enforced)
    return {"products": [], "total": 0, "limit": limit, "offset": offset}


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_product(body: ProductCreate):
    """Create a new product."""
    return {"id": str(uuid.uuid4()), **body.model_dump()}


@router.get("/{product_id}")
async def get_product(product_id: str):
    """Fetch a single product."""
    return {"id": product_id}


@router.patch("/{product_id}")
async def update_product(product_id: str, body: ProductUpdate):
    """Update product details or price."""
    return {"id": product_id, **body.model_dump(exclude_none=True)}


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(product_id: str):
    """Soft-delete a product (sets deleted_at)."""
    # TODO: soft delete, never hard delete — maintain audit trail
    return


@router.post("/{product_id}/stock")
async def adjust_stock(product_id: str, body: StockAdjust):
    """Restock or manually adjust stock for a product."""
    return {
        "product_id": product_id,
        "quantity_delta": body.quantity_delta,
        "reason": body.reason,
        "new_quantity": 0,  # TODO: calculate from DB
    }


@router.post("/import")
async def import_products():
    """AI-powered bulk import from PDF, Excel, or photo.
    Accepts multipart/form-data with file attachment.
    """
    # TODO: accept file upload → call AI extraction service → return preview for user confirmation
    return {"status": "not_implemented", "message": "AI import coming in Phase 4."}
