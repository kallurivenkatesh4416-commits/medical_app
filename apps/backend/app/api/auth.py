"""Auth API (brief §3, PLAN.md Slice 2): OTP login + JWT access/refresh."""

import uuid

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.config import get_settings
from app.models.user import User
from app.security.dashboard_session import (
    clear_dashboard_session_cookies,
    create_dashboard_csrf_token,
    csrf_cookie_session_present,
    dashboard_refresh_cookie,
    dashboard_session_cookie,
    set_dashboard_session_cookies,
    verify_dashboard_csrf,
)
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
    dashboard_session: bool = False


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
    session_transport: str = "bearer"


class CookieSessionOut(BaseModel):
    session_transport: str = "cookie"


class CsrfOut(BaseModel):
    csrf_token: str


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
    response: Response,
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
    if body.dashboard_session:
        set_dashboard_session_cookies(
            response,
            access_token=access,
            refresh_token=refresh,
        )
        return OtpVerifyOut(session_transport="cookie")
    return OtpVerifyOut(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenPair | CookieSessionOut)
def refresh(
    request: Request,
    response: Response,
    body: RefreshIn | None = None,
    session: Session = Depends(get_db),
) -> TokenPair | CookieSessionOut:
    cookie_refresh = dashboard_refresh_cookie(request)
    cookie_flow = body is None and cookie_refresh is not None
    raw_refresh = cookie_refresh if cookie_flow else body.refresh_token if body else None
    if raw_refresh is None:
        raise auth_service.AuthError(401, "invalid_refresh", "Invalid refresh token.")
    if cookie_flow:
        verify_dashboard_csrf(request)
    access, new_refresh = auth_service.rotate_refresh(
        session, raw_refresh=raw_refresh, from_ip=client_ip(request)
    )
    if cookie_flow:
        set_dashboard_session_cookies(
            response,
            access_token=access,
            refresh_token=new_refresh,
            session_id=dashboard_session_cookie(request),
        )
        return CookieSessionOut()
    return TokenPair(access_token=access, refresh_token=new_refresh)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    body: RefreshIn | None = None,
    session: Session = Depends(get_db),
) -> Response:
    cookie_refresh = dashboard_refresh_cookie(request)
    cookie_session = dashboard_session_cookie(request)
    cookie_flow = body is None and (cookie_refresh is not None or cookie_session is not None)
    raw_refresh = cookie_refresh if cookie_flow else body.refresh_token if body else None
    if cookie_flow:
        verify_dashboard_csrf(request)
    if raw_refresh is not None:
        auth_service.logout(session, raw_refresh=raw_refresh, from_ip=client_ip(request))
    if cookie_flow or cookie_session:
        clear_dashboard_session_cookies(response)
    response.status_code = 204
    return response


@router.get("/csrf", response_model=CsrfOut)
def csrf(request: Request) -> CsrfOut:
    if not csrf_cookie_session_present(request):
        raise auth_service.AuthError(
            401,
            "dashboard_session_required",
            "Dashboard session is not available.",
        )
    session_id = dashboard_session_cookie(request)
    assert session_id is not None
    return CsrfOut(csrf_token=create_dashboard_csrf_token(session_id=session_id))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
