"""Internal DTOs for Google OAuth flows."""

from __future__ import annotations

from dataclasses import dataclass

from backend.processes.auth.types import IssuedSession


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
