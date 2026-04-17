"""Auth endpoints: status, register, login, logout.

Single-user gate: /auth/register is rejected if any user already exists. To open
signup later (multi-user mode), drop the `count_users()` check in `register`.
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
from api.schemas import (
    AuthStatusResponse,
    AuthTokenResponse,
    AuthUser,
    LoginRequest,
    RegisterRequest,
)
from config import AUTH_TOKEN_TTL_DAYS
from infra.persistence.runtime_store import RuntimeStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(raw: str) -> str:
    email = raw.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email address",
        )
    return email


def _issue_token(store: RuntimeStore, user_id: int) -> str:
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
        user=AuthUser(id=user.id, email=user.email) if user else None,
    )


@router.post("/register", response_model=AuthTokenResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    store: RuntimeStore = Depends(get_store),
) -> AuthTokenResponse:
    # Single-user gate. Remove this when enabling multi-user signup.
    if store.count_users() > 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is closed — an account already exists on this server.",
        )
    email = _validate_email(payload.email)
    if store.get_user_by_email(email) is not None:
        # Defensive — the count check should block this in single-user mode, but
        # the pattern below survives the flip to multi-user without change.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )
    user = store.create_user(email=email, password_hash=hash_password(payload.password))
    # Backfill any orphan sessions/exports (from before auth existed) to this user
    # so the first logged-in person doesn't see an empty history.
    backfilled_sessions, backfilled_exports = store.backfill_orphan_ownership(user.id)
    if backfilled_sessions or backfilled_exports:
        logger.info(
            "Backfilled %d session(s) and %d export(s) to user %d on first registration",
            backfilled_sessions, backfilled_exports, user.id,
        )
    token = _issue_token(store, user.id)
    return AuthTokenResponse(token=token, user=AuthUser(id=user.id, email=user.email))


@router.post("/login", response_model=AuthTokenResponse)
def login(
    payload: LoginRequest,
    store: RuntimeStore = Depends(get_store),
) -> AuthTokenResponse:
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
    token = _issue_token(store, user.id)
    return AuthTokenResponse(token=token, user=AuthUser(id=user.id, email=user.email))


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
