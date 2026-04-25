"""Internal DTOs for the auth process."""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass


@dataclass
class IssuedSession:
    token: str
    expires_at: datetime


@dataclass
class RegistrationResult:
    user: object
    session: IssuedSession | None = None
    verification_token: str | None = None
