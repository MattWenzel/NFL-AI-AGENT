"""Data types for the auth subsystem.

Exception classes live in `auth/errors.py`. This module holds the value
types carried across auth flows — the authenticated-user view, the
OAuth token bundles, the OIDC identity claims — plus the canonical
identity-provider name strings used as `user_identities.provider`
values.
"""

from __future__ import annotations

from dataclasses import dataclass

from storage import UserRecord


# Identity-provider name strings stored in `user_identities.provider`.
PASSWORD = "password"
GOOGLE = "google"

# Sentinel `users.password_hash` value for OAuth-only accounts (no password
# set). bcrypt treats it as malformed, so `verify_password` always returns
# False and password login is naturally blocked without a schema rebuild.
OAUTH_ONLY_SENTINEL_HASH = "!"


@dataclass
class AuthenticatedUser:
    """Lightweight view of the current user for router code. Shields routers from
    the full UserRecord (which carries password_hash — don't leak it by accident)."""
    id: int
    email: str
    role: str = "user"

    @classmethod
    def from_record(cls, record: UserRecord) -> "AuthenticatedUser":
        return cls(id=record.id, email=record.email, role=record.role)


# ---------------- Codex (ChatGPT) OAuth ----------------

@dataclass
class DeviceCodeStart:
    device_auth_id: str
    user_code: str
    interval: int
    verification_url: str


@dataclass
class DeviceCodeAuthorized:
    """Result of a successful device-code poll — the server returns the code
    *and* the PKCE verifier it generated on the user's behalf."""
    authorization_code: str
    code_verifier: str
    code_challenge: str


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_at: int  # epoch ms
    email: str | None = None


# ---------------- Google OAuth ----------------

@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    email_verified: bool
    name: str | None
