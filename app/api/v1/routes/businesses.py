"""Business and branch management routes."""

from fastapi import APIRouter, status
from pydantic import BaseModel
from typing import Optional
import uuid

router = APIRouter()


class BusinessCreate(BaseModel):
    name: str
    phone: str
    address: Optional[str] = None
    business_type: Optional[str] = None  # e.g. "provision_store", "pharmacy"


class BranchCreate(BaseModel):
    name: str
    address: Optional[str] = None


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_business(body: BusinessCreate):
    """Create a new business (tenant). Called once during onboarding."""
    # TODO: insert into businesses table scoped to authenticated user
    return {
        "id": str(uuid.uuid4()),
        "name": body.name,
        "phone": body.phone,
        "address": body.address,
        "business_type": body.business_type,
        "plan": "free",
    }


@router.get("/{business_id}")
async def get_business(business_id: str):
    """Fetch business profile."""
    # TODO: query DB with RLS check
    return {"id": business_id, "name": "Emeka's Provisions", "plan": "growth"}


@router.patch("/{business_id}")
async def update_business(business_id: str, body: BusinessCreate):
    """Update business profile."""
    return {"id": business_id, **body.model_dump()}


@router.post("/{business_id}/branches", status_code=status.HTTP_201_CREATED)
async def create_branch(business_id: str, body: BranchCreate):
    """Add a new branch to a business."""
    return {"id": str(uuid.uuid4()), "business_id": business_id, **body.model_dump()}


@router.get("/{business_id}/branches")
async def list_branches(business_id: str):
    """List all branches for a business."""
    return {"branches": [], "business_id": business_id}
