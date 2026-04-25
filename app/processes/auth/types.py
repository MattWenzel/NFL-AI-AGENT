"""Internal DTOs for the auth process."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AuditContext:
    """Request-scoped audit metadata passed from routes to services."""

    ip: str | None = None
    user_agent: str | None = None


@dataclass
class IssuedSession:
    token: str
    expires_at: datetime


@dataclass
class RegistrationResult:
    user: object
    session: IssuedSession | None = None
    verification_token: str | None = None
