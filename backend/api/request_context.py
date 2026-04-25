"""Helpers for deriving request-scoped API context."""

from fastapi import Request

from backend.audit import AuditContext


def client_ip(request: Request) -> str | None:
    if request.client is not None:
        return request.client.host
    return None


def audit_from_request(request: Request) -> AuditContext:
    return AuditContext(ip=client_ip(request), user_agent=request.headers.get("User-Agent"))
