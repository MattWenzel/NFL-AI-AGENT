"""Shared auth lifecycle primitives used by password and OAuth flows."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.config import AUTH_TOKEN_TTL_DAYS
from backend.lib.storage import RuntimeStore
from backend.features.auth.errors import AuthConflictError
from backend.features.auth.types import IssuedSession
from backend.lib.auth.primitives import generate_token

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IdentitySeed:
    provider: str
    provider_subject: str
    email: str | None = None
    required: bool = True


async def issue_session(store: RuntimeStore, user_id: int) -> IssuedSession:
    token = generate_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=AUTH_TOKEN_TTL_DAYS)
    await store.create_auth_session(
        token=token,
        user_id=user_id,
        expires_at=expires_at.isoformat(),
    )
    return IssuedSession(token=token, expires_at=expires_at)


async def create_user_account(
    store: RuntimeStore,
    *,
    email: str,
    password_hash: str,
    verified: bool,
    identity: IdentitySeed | None = None,
):
    if await store.get_user_by_email(email) is not None:
        raise AuthConflictError("An account with this email already exists.")
    is_first_user = await store.count_users() == 0
    role = "admin" if is_first_user else "user"
    verified_at = datetime.now(timezone.utc).isoformat() if verified else None
    user = await store.create_user(
        email=email,
        password_hash=password_hash,
        role=role,
        email_verified_at=verified_at,
    )
    if identity is not None:
        try:
            await store.create_identity(
                user_id=user.id,
                provider=identity.provider,
                provider_subject=identity.provider_subject,
                email=identity.email,
            )
        except Exception:
            if identity.required:
                raise
            logger.exception("Failed to seed %s identity for user %d", identity.provider, user.id)
    if is_first_user:
        await store.backfill_orphan_ownership(user.id)
    return user
