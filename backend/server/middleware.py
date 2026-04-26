"""HTTP middleware mounted on the FastAPI app.

Kept separate from `server/app.py` so the factory there is strictly the
application wiring (router includes, CORS, lifespan) and each middleware
has its own file to grow in.
"""

from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from backend.server.request_context import new_request_id, request_id


# Content-Security-Policy. `cdn.jsdelivr.net` is permitted in `script-src`
# because index.html loads marked.js and chart.js from there; vendoring them
# locally and tightening this to 'self' is a worthwhile follow-up but out of
# scope for the initial hardening pass. `style-src 'unsafe-inline'` is
# required because several widgets build HTML via innerHTML with inline
# `style="…"` attributes — moving those to CSS classes would tighten this
# further. `connect-src 'self'` covers fetch + EventSource (SSE); no
# cross-origin backends exist in this app.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Stamp every request with a correlation ID for log tracing.

    Reads `X-Request-ID` from the request if the client supplied one
    (allows tracing across services / from curl); otherwise generates an
    8-hex-char ID. Sets the `request_id` contextvar so every log line
    emitted while handling this request — including from tasks the
    request handler spawns via `asyncio.create_task` and `asyncio.to_thread`
    — carries the ID. Echoes the ID back as `X-Request-ID` so the
    client can correlate.

    Capped at 32 chars so a hostile client can't bloat every log line.
    """

    async def dispatch(self, request: Request, call_next):
        rid = (request.headers.get("X-Request-ID") or new_request_id())[:32]
        token = request_id.set(rid)
        try:
            response = await call_next(request)
            response.headers.setdefault("X-Request-ID", rid)
            return response
        finally:
            request_id.reset(token)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Set standard defensive response headers on every reply.

    HSTS is only emitted when the request arrives over HTTPS — behind Fly's
    TLS terminator `request.url.scheme` reads as "https" via the forwarded
    scheme, so prod gets HSTS while `http://localhost` dev runs don't
    (which would otherwise pin localhost to https in the browser).
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), camera=(), microphone=()"
        )
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response
