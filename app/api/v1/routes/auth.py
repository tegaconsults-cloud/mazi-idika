"""Auth routes — phone OTP registration and login via Supabase Auth."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

router = APIRouter()


class PhoneRequest(BaseModel):
    phone: str  # e.g. "+2348012345678"


class OTPVerifyRequest(BaseModel):
    phone: str
    otp: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/request-otp", status_code=status.HTTP_200_OK)
async def request_otp(body: PhoneRequest):
    """Send OTP to phone number via Supabase Auth."""
    # TODO: call supabase.auth.sign_in_with_otp(phone=body.phone)
    return {"message": "OTP sent", "phone": body.phone}


@router.post("/verify-otp", response_model=TokenResponse)
async def verify_otp(body: OTPVerifyRequest):
    """Verify OTP and return JWT access token."""
    # TODO: call supabase.auth.verify_otp(phone=body.phone, token=body.otp, type="sms")
    # TODO: create or fetch user record in our DB
    # TODO: return create_access_token(user.id)
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="OTP verification not yet wired to Supabase — coming in Phase 1.",
    )


@router.post("/refresh")
async def refresh_token():
    """Refresh an expired access token."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Coming soon.")
