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

import bcrypt
from fastapi import Request

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


SESSION_COOKIE_NAME = "session"
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"


def _extract_bearer(request: Request) -> str | None:
    """Legacy helper. Prefer `_extract_session_token`.

    Returns only a Bearer-header token (no cookie fallback) so callers that
    specifically need to know the request is API-client-shaped (not browser-
    shaped) can branch on it — e.g. the CSRF dep uses this to opt out of
    CSRF checks for Bearer requests.
    """
    header = request.headers.get("Authorization") or request.headers.get("authorization")
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _extract_session_token(request: Request) -> str | None:
    """Return the session token from Bearer header (preferred) or cookie.

    Bearer wins when explicitly set — an API client sending
    `Authorization: Bearer …` means "use this token specifically", and any
    stale cookie from an earlier session on the same client (TestClient
    persistence, a human switching accounts in curl) should not silently
    override it. Browsers never send Bearer on cross-origin requests, so
    in the normal UI flow only the cookie is present and that branch runs.
    """
    header_token = _extract_bearer(request)
    if header_token:
        return header_token
    return request.cookies.get(SESSION_COOKIE_NAME)
