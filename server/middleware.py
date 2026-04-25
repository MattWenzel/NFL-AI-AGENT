"""HTTP middleware mounted on the FastAPI app.

Kept separate from `server/app.py` so the factory there is strictly the
application wiring (router includes, CORS, lifespan) and each middleware
has its own file to grow in.
"""

from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


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
