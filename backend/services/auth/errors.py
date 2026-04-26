"""Auth service errors.

`AuthConflictError` lives in `lib/auth/errors.py` because it's raised by
the lib-level `create_user_account` helper. Importers should reach for
the canonical lib path. The errors here are the ones that only make
sense in a user-facing flow (validation, credentials, lockout,
unverified email).
"""

from __future__ import annotations


class AuthServiceError(Exception):
    pass


class AuthValidationError(AuthServiceError):
    pass


class AuthCredentialsError(AuthServiceError):
    pass


class AuthLockedError(AuthServiceError):
    """Raised when an account is temporarily locked after too many failures."""

    def __init__(self, retry_after_seconds: int, message: str = "Account temporarily locked"):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class AuthEmailUnverifiedError(AuthServiceError):
    """Raised when email verification is required and the account is unverified."""
