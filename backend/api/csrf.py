"""CSRF protection via the double-submit-cookie pattern.

On login we set two cookies: `session` (HttpOnly — the real auth material)
and `csrf_token` (readable by JS — the anti-CSRF proof). For mutating
requests (POST/PUT/PATCH/DELETE) originating from the browser, the JS
reads `csrf_token` from document.cookie and echoes it in the
`X-CSRF-Token` header. We then compare the header to the cookie — they
match for same-origin requests (the browser sends both) but not for
cross-origin CSRF (the attacker's script can't read our cookie).

Bearer-authenticated requests (API clients, `/docs`) skip the check
because a cross-origin attacker can't forge the Authorization header in
a browser. This keeps the OpenAPI UI and curl/httpie scripts working
without CSRF ceremony while the browser UI gets protection.

Safe methods (GET/HEAD/OPTIONS) also skip — by convention they don't
mutate state, and enforcing CSRF on them would break the many read-only
routes without adding protection.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from backend.api.session_tokens import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, SESSION_COOKIE_NAME, _extract_bearer
from backend.persistence.audit_events import AuditEvent

logger = logging.getLogger(__name__)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class CSRFViolation:
    reason: str


def generate_csrf_token() -> str:
    """32 random bytes, urlsafe base64. Not secret (JS reads it), but must
    be unpredictable so an attacker can't guess it and precompute the header."""
    return secrets.token_urlsafe(32)


def verify_csrf(request: Request) -> None:
    """FastAPI dependency. Raises 403 on CSRF mismatch, otherwise no-op.

    Wire at the router level (not per-route) for any router whose handlers
    mutate state. Exempting read-only routers avoids paying the check cost
    and keeps the OpenAPI docs usable over GET.

    CSRF is only enforced when the caller is authenticated via the session
    cookie. Three skip cases:
      1. Safe method (GET/HEAD/OPTIONS) — no state change.
      2. Bearer header present — API-client-shaped, not browser-reachable
         via CSRF (browsers don't auto-attach Authorization cross-origin).
      3. No session cookie — either the request is unauthenticated (will
         401 at `get_current_user`) or the test harness is using dependency
         overrides. Either way, there's no cookie-auth to exploit.
    """
    if request.method in _SAFE_METHODS:
        return
    if _extract_bearer(request) is not None:
        return
    if SESSION_COOKIE_NAME not in request.cookies:
        return
    cookie_value = request.cookies.get(CSRF_COOKIE_NAME)
    header_value = request.headers.get(CSRF_HEADER_NAME)
    if not cookie_value or not header_value:
        _fail(request, reason="missing_csrf_token")
    if not secrets.compare_digest(cookie_value, header_value):
        _fail(request, reason="csrf_token_mismatch")


def _fail(request: Request, *, reason: str) -> None:
    # Record for audit. `asyncio.create_task` needs a running event loop —
    # FastAPI routes always run in one, so this path works in prod. Tests
    # that call the dep synchronously (no loop) fall into the RuntimeError
    # branch; the 403 still raises either way.
    try:
        import asyncio

        loop = asyncio.get_running_loop()
        store = getattr(request.app.state, "store", None)
        if store is not None:
            loop.create_task(
                store.record_security_event(
                    event_type=AuditEvent.CSRF_REJECTED,
                    ip=_client_ip(request),
                    user_agent=request.headers.get("User-Agent"),
                    metadata={"reason": reason, "path": request.url.path, "method": request.method},
                )
            )
    except RuntimeError:
        # No running loop (sync test harness). Skip the audit; we still 403.
        pass
    except Exception:  # noqa: BLE001 — audit write is best-effort
        logger.exception("Failed to record csrf_rejected audit event")
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="CSRF token missing or invalid",
    )


def _client_ip(request: Request) -> str | None:
    if request.client is not None:
        return request.client.host
    return None
