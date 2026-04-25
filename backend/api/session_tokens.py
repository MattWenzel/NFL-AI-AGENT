"""Request helpers for browser-cookie and Bearer-token auth."""

from fastapi import Request

SESSION_COOKIE_NAME = "session"
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"


def _extract_bearer(request: Request) -> str | None:
    """Return a Bearer-header token without considering cookies."""
    header = request.headers.get("Authorization") or request.headers.get("authorization")
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _extract_session_token(request: Request) -> str | None:
    """Return the session token from Bearer header (preferred) or cookie."""
    header_token = _extract_bearer(request)
    if header_token:
        return header_token
    return request.cookies.get(SESSION_COOKIE_NAME)
