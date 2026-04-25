"""Internal DTOs shared across the server application-service layer.

Distinct from `server/schemas/*` (Pydantic wire-format models for HTTP):
these are plain dataclasses passed between services, or returned from
services to their routes. They never cross the network boundary as-is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from provider import BaseLLMClient
from storage import SessionRecord


# ---------------- auth ----------------

@dataclass(frozen=True)
class AuditContext:
    """Request-scoped audit metadata passed from routes to the service.

    Services stay free of FastAPI imports; routes pull `ip`/`user_agent`
    off `Request` and hand them to the service via this dataclass.
    """
    ip: str | None = None
    user_agent: str | None = None


@dataclass
class IssuedSession:
    token: str
    expires_at: datetime


@dataclass
class RegistrationResult:
    """One of: (a) a fully-issued session for the just-registered user, or
    (b) a "verification pending" signal telling the caller to send a
    verification email and not set session cookies."""
    user: object
    session: IssuedSession | None = None
    verification_token: str | None = None


# ---------------- chat ----------------

@dataclass
class PreparedChat:
    client: BaseLLMClient
    provider_name: str
    session: SessionRecord


@dataclass
class ToolCallLogEntry:
    tool_run_id: str
    tool: str
    input: dict
    result_preview: str = ""


# ---------------- google OAuth ----------------

@dataclass(frozen=True)
class SignInOutcome:
    user_id: int
    user_email: str
    user_role: str
    session: IssuedSession
    is_new_user: bool


@dataclass(frozen=True)
class LinkOutcome:
    user_id: int


@dataclass(frozen=True)
class IdentitySummary:
    provider: str
    display: str
    linked_at: str
    removable: bool
