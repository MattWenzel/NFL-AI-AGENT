"""Google OAuth process service errors."""

from __future__ import annotations


class GoogleOAuthServiceError(Exception):
    """Base class for Google OAuth flow failures."""


class GoogleOAuthDisabledError(GoogleOAuthServiceError):
    pass


class GoogleOAuthInvalidStateError(GoogleOAuthServiceError):
    pass


class GoogleOAuthEmailUnverifiedError(GoogleOAuthServiceError):
    pass


class GoogleOAuthLinkConflictError(GoogleOAuthServiceError):
    """Raised when the Google identity is already linked to a different user."""


class GoogleOAuthLastIdentityError(GoogleOAuthServiceError):
    """Raised when unlinking would leave the user with no login method."""
