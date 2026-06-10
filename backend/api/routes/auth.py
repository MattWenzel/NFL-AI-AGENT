"""Auth endpoints: status, register, login, logout, verify-email, resend.

Cookie-based session auth (HttpOnly+Secure+SameSite=Lax) with a CSRF
double-submit cookie for browser clients. The AuthTokenResponse body still
returns the token so API clients sending `Authorization: Bearer …` keep
working (and `/docs` stays usable); the frontend ignores it and relies on
cookies.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from backend.server.session import (
    _extract_session_token,
    clear_auth_cookies,
    rotate_csrf_cookie,
    set_auth_cookies,
)
from backend.domain.auth.types import AuthenticatedUser
from backend.config import EMAIL_VERIFICATION_REQUIRED
from backend.server.csrf import verify_csrf
from backend.api.dependencies import (
    get_auth_service,
    get_current_user,
    get_current_user_optional,
    get_process_state,
)
from backend.server.request_context import audit_from_request
from backend.server.process_state import AppProcessState
from backend.api.schemas.auth import (
    AuthStatusResponse,
    AuthTokenResponse,
    AuthUser,
    DeleteAccountRequest,
    LoginRequest,
    PasswordChangeRequest,
    RegisterRequest,
    RegistrationPendingResponse,
    RequestPasswordResetRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    VerifyEmailRequest,
)
from backend.api.schemas.common import OkResponse
from backend.domain.auth.errors import AuthConflictError
from backend.application.auth import (
    AuthCredentialsError,
    AuthEmailUnverifiedError,
    AuthInviteCodeError,
    AuthLockedError,
    AuthService,
    AuthValidationError,
)

logger = logging.getLogger(__name__)

# Note: /auth endpoints that run BEFORE a session exists (register, login,
# verify-email) can't be CSRF-protected via cookie-match — there's no cookie
# yet. They rely on the rate limiter + Pydantic validation + the bcrypt-paced
# login path instead. Post-auth endpoints (logout, password, DELETE /me,
# resend) opt in to CSRF per-route below.
router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(
    service: AuthService = Depends(get_auth_service),
    user: AuthenticatedUser | None = Depends(get_current_user_optional),
) -> AuthStatusResponse:
    return await service.auth_status(user)


@router.post("/register")
async def register(
    payload: RegisterRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
    process_state: AppProcessState = Depends(get_process_state),
):
    process_state.register_limiter.check(request)
    try:
        result = await service.register(
            email=payload.email,
            password=payload.password,
            invite_code=payload.invite_code,
            audit=audit_from_request(request),
        )
    except AuthConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except AuthInviteCodeError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except AuthValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if result.session is None:
        # Verification required — no cookies set, client shows "check email" UI.
        body = RegistrationPendingResponse(email=result.user.email).model_dump()
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=body)

    body = AuthTokenResponse(
        token=result.session.token,
        user=AuthUser(id=result.user.id, email=result.user.email, role=result.user.role),
    ).model_dump()
    response = JSONResponse(status_code=status.HTTP_201_CREATED, content=body)
    set_auth_cookies(response=response, session=result.session, request=request)
    return response


@router.post("/login", response_model=AuthTokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
    process_state: AppProcessState = Depends(get_process_state),
) -> AuthTokenResponse:
    process_state.login_limiter.check(request)
    try:
        user, session = await service.login(
            email=payload.email,
            password=payload.password,
            audit=audit_from_request(request),
        )
    except AuthLockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )
    except AuthEmailUnverifiedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
            headers={"X-Error-Code": "email_not_verified"},
        )
    except AuthCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    set_auth_cookies(response=response, session=session, request=request)
    return AuthTokenResponse(
        token=session.token,
        user=AuthUser(id=user.id, email=user.email, role=user.role),
    )


@router.post("/verify-email", response_model=AuthTokenResponse)
async def verify_email(
    payload: VerifyEmailRequest,
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
) -> AuthTokenResponse:
    try:
        user, session = await service.verify_email(payload.token, audit=audit_from_request(request))
    except AuthValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    set_auth_cookies(response=response, session=session, request=request)
    return AuthTokenResponse(
        token=session.token,
        user=AuthUser(id=user.id, email=user.email, role=user.role),
    )


@router.post("/resend-verification", response_model=OkResponse)
async def resend_verification(
    payload: ResendVerificationRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
    process_state: AppProcessState = Depends(get_process_state),
) -> OkResponse:
    process_state.register_limiter.check(request)
    # Per-email cap on top of the per-IP one — without it an IP-rotating
    # bot can burn the Resend sending quota.
    process_state.email_resend_limiter.check_key(payload.email.strip().lower())
    # Always return ok=True to avoid leaking which emails are registered.
    await service.resend_verification(payload.email, audit=audit_from_request(request))
    return OkResponse(ok=True)


@router.post("/request-password-reset", response_model=OkResponse)
async def request_password_reset(
    payload: RequestPasswordResetRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
    process_state: AppProcessState = Depends(get_process_state),
) -> OkResponse:
    process_state.register_limiter.check(request)
    # Per-email cap on top of the per-IP one — shared with verification
    # resends, since both burn the Resend sending quota.
    process_state.email_resend_limiter.check_key(payload.email.strip().lower())
    # Always return ok=True to avoid leaking which emails are registered.
    await service.request_password_reset(payload.email, audit=audit_from_request(request))
    return OkResponse(ok=True)


@router.post("/reset-password", response_model=AuthTokenResponse)
async def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
    process_state: AppProcessState = Depends(get_process_state),
) -> AuthTokenResponse:
    process_state.login_limiter.check(request)
    try:
        user, session = await service.reset_password(
            token=payload.token,
            new_password=payload.new_password,
            audit=audit_from_request(request),
        )
    except AuthValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    set_auth_cookies(response=response, session=session, request=request)
    return AuthTokenResponse(
        token=session.token,
        user=AuthUser(id=user.id, email=user.email, role=user.role),
    )


@router.post("/logout", dependencies=[Depends(verify_csrf)])
async def logout(
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    await service.logout(
        _extract_session_token(request),
        user_id=user.id,
        audit=audit_from_request(request),
    )
    clear_auth_cookies(response, request)
    return {"ok": True}


@router.put("/password", response_model=OkResponse, dependencies=[Depends(verify_csrf)])
async def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
    process_state: AppProcessState = Depends(get_process_state),
    user: AuthenticatedUser = Depends(get_current_user),
) -> OkResponse:
    process_state.account_limiter.check(request)
    try:
        await service.change_password(
            user=user,
            current_password=payload.current_password,
            new_password=payload.new_password,
            keep_token=_extract_session_token(request) or "",
            audit=audit_from_request(request),
        )
    except AuthCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    # Rotate the CSRF cookie — any in-flight CSRF attack's stolen token is
    # now stale. The session cookie stays (current session is the keep_token).
    rotate_csrf_cookie(response, request)
    return OkResponse(ok=True)


@router.delete("/me", response_model=OkResponse, dependencies=[Depends(verify_csrf)])
async def delete_account(
    payload: DeleteAccountRequest,
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
    process_state: AppProcessState = Depends(get_process_state),
    user: AuthenticatedUser = Depends(get_current_user),
) -> OkResponse:
    process_state.account_limiter.check(request)
    try:
        deleted_files = await service.delete_account(
            user=user,
            password=payload.password,
            audit=audit_from_request(request),
        )
    except AuthCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    logger.info("Deleted user %d (%s); %d CSV file(s) removed", user.id, user.email, deleted_files)
    clear_auth_cookies(response, request)
    return OkResponse(ok=True)
