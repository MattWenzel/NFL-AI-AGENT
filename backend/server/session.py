"""HTTP session helpers for browser-cookie and Bearer-token auth."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import Request, Response

from backend.lib.auth.types import IssuedSession

SESSION_COOKIE_NAME = "session"
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"


def generate_csrf_token() -> str:
    """Return an unpredictable token for the readable CSRF cookie."""
    return secrets.token_urlsafe(32)


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


def _cookie_max_age_seconds(session: IssuedSession) -> int:
    # Cookie Max-Age aligned to the session's DB expiry.
    delta = session.expires_at - datetime.now(timezone.utc)
    return max(60, int(delta.total_seconds()))


def is_secure_request(request: Request) -> bool:
    """Whether cookies should carry the Secure flag."""
    return request.url.scheme == "https"


def set_auth_cookies(
    *,
    response: Response,
    session: IssuedSession,
    request: Request,
) -> None:
    """Apply session + CSRF cookies to the response."""
    max_age = _cookie_max_age_seconds(session)
    secure = is_secure_request(request)
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


def rotate_csrf_cookie(response: Response, request: Request) -> None:
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=generate_csrf_token(),
        httponly=False,
        secure=is_secure_request(request),
        samesite="lax",
        path="/",
    )


def clear_auth_cookies(response: Response, request: Request) -> None:
    secure = is_secure_request(request)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", secure=secure, samesite="lax")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/", secure=secure, samesite="lax")
