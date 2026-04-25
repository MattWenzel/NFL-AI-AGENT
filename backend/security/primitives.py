"""Auth primitives: password hashing and token generation.

Multi-user password auth. Registration is open; first registrant becomes
the admin (see `backend/api/routes/auth.py`). OAuth sign-in/link flows
reuse the same opaque, revocable auth_sessions tokens.
"""

from __future__ import annotations

import logging
import secrets

import bcrypt

logger = logging.getLogger(__name__)


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
