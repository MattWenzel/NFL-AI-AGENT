"""Cookie helpers for browser session authentication."""

from datetime import datetime, timezone

from fastapi import Request, Response

from backend.api.csrf import generate_csrf_token
from backend.api.session_tokens import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from backend.processes.auth.types import IssuedSession


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
