"""Auth primitives: password hashing, token generation, and bearer parsing.

Multi-user password auth. Registration is open; first registrant becomes
the admin (see `server/routes/auth.py`). OAuth is not implemented — when
it is, the session-token mechanism here is reused as-is (opaque bearer
tokens in auth_sessions, revocable). See CLAUDE.md's "OAuth migration
path" section for the slotting plan.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

import bcrypt
from fastapi import Request

from storage import UserRecord

logger = logging.getLogger(__name__)


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


def hash_password(plain: str) -> str:
    """bcrypt hash with default cost factor (12). Returns a utf-8 string suitable for storage."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        # Malformed hash string — treat as non-match rather than raising.
        return False


def generate_token() -> str:
    """32 random bytes, base64-urlsafe. ~256 bits of entropy, collision-resistant."""
    return secrets.token_urlsafe(32)


def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization") or request.headers.get("authorization")
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None
