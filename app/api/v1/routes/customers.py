"""Customer management routes."""

from fastapi import APIRouter, Query, status
from pydantic import BaseModel
from typing import Optional
import uuid

router = APIRouter()


class CustomerCreate(BaseModel):
    branch_id: str
    name: str
    phone: Optional[str] = None
    notes: Optional[str] = None


@router.get("/")
async def list_customers(
    branch_id: str = Query(...),
    search: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    return {"customers": [], "total": 0}


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_customer(body: CustomerCreate):
    return {"id": str(uuid.uuid4()), **body.model_dump()}


@router.get("/{customer_id}")
async def get_customer(customer_id: str):
    """Fetch customer profile with purchase history summary."""
    return {
        "id": customer_id,
        "total_spent": 0,
        "total_outstanding": 0,
        "transactions": [],
    }


@router.patch("/{customer_id}")
async def update_customer(customer_id: str, body: CustomerCreate):
    return {"id": customer_id, **body.model_dump(exclude_none=True)}
