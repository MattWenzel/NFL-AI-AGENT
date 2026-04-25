"""Auth process service errors."""

from __future__ import annotations


class AuthServiceError(Exception):
    pass


class AuthConflictError(AuthServiceError):
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
