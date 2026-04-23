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

from auth.primitives import (
    AuthenticatedUser,
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    _extract_session_token,
)
from config import EMAIL_VERIFICATION_REQUIRED
from server.csrf import generate_csrf_token, verify_csrf
from server.dependencies import (
    get_auth_service,
    get_current_user,
    get_current_user_optional,
    get_process_state,
)
from server.process_state import AppProcessState
from server.schemas.auth import (
    AuthOkResponse,
    AuthStatusResponse,
    AuthTokenResponse,
    AuthUser,
    DeleteAccountRequest,
    LoginRequest,
    PasswordChangeRequest,
    RegisterRequest,
    RegistrationPendingResponse,
    ResendVerificationRequest,
    VerifyEmailRequest,
)
from server.services.auth import (
    AuditContext,
    AuthCredentialsError,
    AuthConflictError,
    AuthEmailUnverifiedError,
    AuthLockedError,
    AuthService,
    AuthValidationError,
    IssuedSession,
)

logger = logging.getLogger(__name__)

# Note: /auth endpoints that run BEFORE a session exists (register, login,
# verify-email) can't be CSRF-protected via cookie-match — there's no cookie
# yet. They rely on the rate limiter + Pydantic validation + the bcrypt-paced
# login path instead. Post-auth endpoints (logout, password, DELETE /me,
# resend) opt in to CSRF per-route below.
router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    if request.client is not None:
        return request.client.host
    return None


def _audit_from(request: Request) -> AuditContext:
    return AuditContext(ip=_client_ip(request), user_agent=request.headers.get("User-Agent"))


def _cookie_max_age_seconds(session: IssuedSession) -> int:
    # Cookie Max-Age aligned to the session's DB expiry.
    from datetime import datetime, timezone

    delta = session.expires_at - datetime.now(timezone.utc)
    return max(60, int(delta.total_seconds()))


def _is_secure_request(request: Request) -> bool:
    """Whether cookies should carry the Secure flag.

    In prod behind Fly/Caddy TLS, request.url.scheme is "https" via the
    proxy-forwarded scheme. In local dev it's "http" — setting Secure there
    would make the cookie invisible to the browser and break sign-in.
    """
    return request.url.scheme == "https"


def _set_auth_cookies(
    *,
    response: Response,
    session: IssuedSession,
    request: Request,
) -> None:
    """Apply session + CSRF cookies to the response.

    Session cookie: HttpOnly — JS cannot read it, only the server.
    CSRF cookie:    not HttpOnly — JS must read it to echo in X-CSRF-Token.
    Both:           SameSite=Lax so the session survives OAuth callbacks.
    """
    max_age = _cookie_max_age_seconds(session)
    secure = _is_secure_request(request)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session.token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=generate_csrf_token(),
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _clear_auth_cookies(response: Response, request: Request) -> None:
    secure = _is_secure_request(request)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", secure=secure, samesite="lax")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/", secure=secure, samesite="lax")


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
            audit=_audit_from(request),
        )
    except AuthConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except AuthValidationError as exc:
        message = str(exc)
        status_code = status.HTTP_403_FORBIDDEN if message == "Invalid invite code" else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=message)

    if result.session is None:
        # Verification required — no cookies set, client shows "check email" UI.
        body = RegistrationPendingResponse(email=result.user.email).model_dump()
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=body)

    body = AuthTokenResponse(
        token=result.session.token,
        user=AuthUser(id=result.user.id, email=result.user.email, role=result.user.role),
    ).model_dump()
    response = JSONResponse(status_code=status.HTTP_201_CREATED, content=body)
    _set_auth_cookies(response=response, session=result.session, request=request)
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
            audit=_audit_from(request),
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

    _set_auth_cookies(response=response, session=session, request=request)
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
        user, session = await service.verify_email(payload.token, audit=_audit_from(request))
    except AuthValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    _set_auth_cookies(response=response, session=session, request=request)
    return AuthTokenResponse(
        token=session.token,
        user=AuthUser(id=user.id, email=user.email, role=user.role),
    )


@router.post("/resend-verification", response_model=AuthOkResponse)
async def resend_verification(
    payload: ResendVerificationRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
    process_state: AppProcessState = Depends(get_process_state),
) -> AuthOkResponse:
    process_state.register_limiter.check(request)
    # Always return ok=True to avoid leaking which emails are registered.
    await service.resend_verification(payload.email, audit=_audit_from(request))
    return AuthOkResponse(ok=True)


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
        audit=_audit_from(request),
    )
    _clear_auth_cookies(response, request)
    return {"ok": True}


@router.put("/password", response_model=AuthOkResponse, dependencies=[Depends(verify_csrf)])
async def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
    process_state: AppProcessState = Depends(get_process_state),
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthOkResponse:
    process_state.account_limiter.check(request)
    try:
        await service.change_password(
            user=user,
            current_password=payload.current_password,
            new_password=payload.new_password,
            keep_token=_extract_session_token(request) or "",
            audit=_audit_from(request),
        )
    except AuthCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    # Rotate the CSRF cookie — any in-flight CSRF attack's stolen token is
    # now stale. The session cookie stays (current session is the keep_token).
    secure = _is_secure_request(request)
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=generate_csrf_token(),
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return AuthOkResponse(ok=True)


@router.delete("/me", response_model=AuthOkResponse, dependencies=[Depends(verify_csrf)])
async def delete_account(
    payload: DeleteAccountRequest,
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
    process_state: AppProcessState = Depends(get_process_state),
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthOkResponse:
    process_state.account_limiter.check(request)
    try:
        deleted_files = await service.delete_account(
            user=user,
            password=payload.password,
            audit=_audit_from(request),
        )
    except AuthCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    logger.info("Deleted user %d (%s); %d CSV file(s) removed", user.id, user.email, deleted_files)
    _clear_auth_cookies(response, request)
    return AuthOkResponse(ok=True)
