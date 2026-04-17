"""Auth endpoints: status, register, login, logout.

Open multi-user password auth. First registrant becomes admin; subsequent
users get role='user'. The internals are factored so a future OAuth
callback (see CLAUDE.md's "OAuth migration path") can reuse the same
create-user-from-verified-identity → issue-session tail without touching
the password path.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from api.auth import (
    AuthenticatedUser,
    generate_token,
    get_current_user,
    get_current_user_optional,
    hash_password,
    verify_password,
)
from api.dependencies import get_store
from api.rate_limit import RateLimiter
from api.schemas import (
    AuthStatusResponse,
    AuthTokenResponse,
    AuthUser,
    LoginRequest,
    RegisterRequest,
)
from config import AUTH_TOKEN_TTL_DAYS
from infra.persistence.runtime_store import RuntimeStore, UserRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Per-IP brute-force guards. Windows are conservative for a self-hosted app;
# bump (or swap for slowapi/redis) if real traffic ever hits this.
_register_limiter = RateLimiter(max_attempts=5, window_seconds=15 * 60)
_login_limiter = RateLimiter(max_attempts=10, window_seconds=15 * 60)


def _to_auth_user(user: AuthenticatedUser | UserRecord) -> AuthUser:
    return AuthUser(id=user.id, email=user.email, role=user.role)


def _validate_password_credentials(email: str, password: str) -> str:
    """Normalize + validate registration inputs. Returns the canonical email.

    Password length is already enforced by pydantic (`RegisterRequest`), so
    this just handles email shape. Raises HTTPException on bad input.
    """
    normalized = email.strip().lower()
    if not _EMAIL_RE.match(normalized):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email address",
        )
    return normalized


def _create_user_from_verified_identity(
    store: RuntimeStore,
    *,
    email: str,
    password_hash: str,
    verified: bool,
) -> UserRecord:
    """Create a user row from a verified identity (password or, later, OAuth).

    The first-ever user becomes role='admin' and inherits any pre-existing
    orphan sessions/exports via backfill. Subsequent users get role='user'
    and start with a clean slate.

    When OAuth ships, the callback will call this with the IdP-supplied
    email and `verified=True` — identical tail to the password path.
    """
    if store.get_user_by_email(email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )
    is_first_user = store.count_users() == 0
    role = "admin" if is_first_user else "user"
    verified_at = datetime.now(timezone.utc).isoformat() if verified else None
    user = store.create_user(
        email=email,
        password_hash=password_hash,
        role=role,
        email_verified_at=verified_at,
    )
    if is_first_user:
        backfilled_sessions, backfilled_exports = store.backfill_orphan_ownership(user.id)
        if backfilled_sessions or backfilled_exports:
            logger.info(
                "Backfilled %d session(s) and %d export(s) to first user %d (admin)",
                backfilled_sessions, backfilled_exports, user.id,
            )
    return user


def _issue_session(store: RuntimeStore, user_id: int) -> str:
    token = generate_token()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=AUTH_TOKEN_TTL_DAYS)
    ).isoformat()
    store.create_auth_session(token=token, user_id=user_id, expires_at=expires_at)
    return token


@router.get("/status", response_model=AuthStatusResponse)
def auth_status(
    store: RuntimeStore = Depends(get_store),
    user: AuthenticatedUser | None = Depends(get_current_user_optional),
) -> AuthStatusResponse:
    return AuthStatusResponse(
        has_users=store.count_users() > 0,
        authenticated=user is not None,
        user=_to_auth_user(user) if user else None,
    )


@router.post("/register", response_model=AuthTokenResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    request: Request,
    store: RuntimeStore = Depends(get_store),
) -> AuthTokenResponse:
    _register_limiter.check(request)
    email = _validate_password_credentials(payload.email, payload.password)
    user = _create_user_from_verified_identity(
        store,
        email=email,
        password_hash=hash_password(payload.password),
        verified=False,
    )
    token = _issue_session(store, user.id)
    return AuthTokenResponse(token=token, user=_to_auth_user(user))


@router.post("/login", response_model=AuthTokenResponse)
def login(
    payload: LoginRequest,
    request: Request,
    store: RuntimeStore = Depends(get_store),
) -> AuthTokenResponse:
    _login_limiter.check(request)
    email = payload.email.strip().lower()
    user = store.get_user_by_email(email)
    # Same response shape + timing for both unknown-user and bad-password to
    # avoid leaking which half is wrong. bcrypt.checkpw handles the timing side;
    # we still run verify_password against a dummy hash when the user is unknown
    # so the request-time doesn't reveal existence.
    dummy_hash = "$2b$12$CwTycUXWue0Thq9StjUM0uJ8.zYtCbCpTqiq2CkP.QrTq3QSnGXFm"
    target_hash = user.password_hash if user else dummy_hash
    if not verify_password(payload.password, target_hash) or user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    token = _issue_session(store, user.id)
    return AuthTokenResponse(token=token, user=_to_auth_user(user))


@router.post("/logout")
def logout(
    request: Request,
    store: RuntimeStore = Depends(get_store),
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    # Parse the token ourselves so we can delete it (get_current_user intentionally hides it).
    header = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    parts = header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        store.delete_auth_session(parts[1].strip())
    return {"ok": True}
