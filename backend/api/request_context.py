"""Helpers for deriving request-scoped API context."""

from fastapi import Request

from backend.audit import AuditContext


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
