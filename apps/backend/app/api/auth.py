"""Auth API (brief §3, PLAN.md Slice 2): OTP login + JWT access/refresh."""

import uuid

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.config import get_settings
from app.models.user import User
from app.security.deps import client_ip, get_current_user, get_db
from app.security.jwt import create_registration_token
from app.services import auth_service

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_PHONE = Field(min_length=8, max_length=20, pattern=r"^\+?[0-9]{8,15}$")


class OtpRequestIn(BaseModel):
    phone: str = _PHONE


class OtpVerifyIn(BaseModel):
    phone: str = _PHONE
    code: str = Field(min_length=4, max_length=10)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=10)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: uuid.UUID
    phone: str
    role: str
    project_id: uuid.UUID | None
    full_name: str | None


class OtpRequestOut(BaseModel):
    sent: bool = True
    # Dev-only convenience so local/CI can complete login without a real SMS.
    # Never populated outside app_env == "local".
    dev_otp: str | None = None


class OtpVerifyOut(BaseModel):
    # Either a token pair (existing account) or a registration grant (new
    # phone -> onboarding). Exactly one side is populated.
    registration_required: bool = False
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    registration_token: str | None = None


@router.post("/otp/request", response_model=OtpRequestOut)
def otp_request(
    body: OtpRequestIn,
    request: Request,
    session: Session = Depends(get_db),
) -> OtpRequestOut:
    code = auth_service.request_otp(
        session, phone=body.phone, from_ip=client_ip(request)
    )
    expose = get_settings().app_env == "local"
    return OtpRequestOut(sent=True, dev_otp=code if expose else None)


@router.post("/otp/verify", response_model=OtpVerifyOut)
def otp_verify(
    body: OtpVerifyIn,
    request: Request,
    session: Session = Depends(get_db),
) -> OtpVerifyOut:
    ip = client_ip(request)
    user = auth_service.consume_otp(
        session, phone=body.phone, code=body.code, from_ip=ip
    )
    if user is None:
        # Verified phone, no account yet -> hand back a registration grant.
        return OtpVerifyOut(
            registration_required=True,
            registration_token=create_registration_token(phone=body.phone),
        )
    access, refresh = auth_service.login_user(session, user=user, from_ip=ip)
    return OtpVerifyOut(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenPair)
def refresh(
    body: RefreshIn,
    request: Request,
    session: Session = Depends(get_db),
) -> TokenPair:
    access, new_refresh = auth_service.rotate_refresh(
        session, raw_refresh=body.refresh_token, from_ip=client_ip(request)
    )
    return TokenPair(access_token=access, refresh_token=new_refresh)


@router.post("/logout", status_code=204)
def logout(
    body: RefreshIn,
    request: Request,
    session: Session = Depends(get_db),
) -> Response:
    auth_service.logout(
        session, raw_refresh=body.refresh_token, from_ip=client_ip(request)
    )
    return Response(status_code=204)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
