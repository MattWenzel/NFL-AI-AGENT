"""Internal DTOs for the auth service.

`IssuedSession` lives in `lib/auth/types.py` (it's the result of the
lib-level `issue_session` helper). Importers should reach for the
canonical lib path.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.lib.auth.types import IssuedSession


@dataclass
class RegistrationResult:
    user: object
    session: IssuedSession | None = None
    verification_token: str | None = None
