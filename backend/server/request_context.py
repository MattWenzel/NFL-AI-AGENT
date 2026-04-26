"""Helpers for deriving request-scoped API context."""

import secrets
from contextvars import ContextVar

from fastapi import Request

from backend.lib.auth.audit import AuditContext


# Per-request correlation ID. Set by RequestIDMiddleware on every incoming
# request, read by the RequestIDFilter in `server/logging.py` to stamp every
# log record. The default ("-") shows up on log lines emitted outside any
# request context (startup, lifespan, background sweeps).
#
# The contextvar lives in server/ because middleware sets it and the logging
# filter reads it — both are server-tier. Lib code never imports this; the
# filter pulls the value transparently into every log record.
request_id: ContextVar[str] = ContextVar("request_id", default="-")


def new_request_id() -> str:
    """Generate a short, grep-friendly request ID (8 hex chars).

    Short on purpose — we want it readable in console output and easy to
    eyeball-correlate across log lines. Collisions don't matter; this is
    a tracing tag, not a primary key.
    """
    return secrets.token_hex(4)


def client_ip(request: Request) -> str | None:
    """Best-effort client IP from the socket address.

    Local deployments should not trust forwarded headers by default. A reverse
    proxy setup can replace this helper when it has a trusted proxy boundary.
    """
    if request.client is not None:
        return request.client.host
    return None


def client_ip_key(request: Request) -> str:
    return client_ip(request) or "unknown"


def audit_from_request(request: Request) -> AuditContext:
    return AuditContext(ip=client_ip(request), user_agent=request.headers.get("User-Agent"))
