"""Exceptions raised by the server application-service layer.

Each service module (auth.py, chat.py, conversations.py, …) owns a small
hierarchy rooted at a `<Name>ServiceError` base. Routes catch specific
subclasses to translate them to HTTP responses. Consolidating them here
keeps the service modules focused on orchestration.
"""

from __future__ import annotations


# ---------------- auth ----------------

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
    """Raised when EMAIL_VERIFICATION_REQUIRED=1 and the account is unverified."""


# ---------------- chat ----------------

class ChatServiceError(Exception):
    """Base class for application-service chat failures."""


class ChatNotFoundError(ChatServiceError):
    """The referenced conversation does not exist for the caller."""


class ChatConfigurationError(ChatServiceError):
    """Provider/credential/model selection failed in an application-specific way."""


# ---------------- conversations ----------------

class ConversationServiceError(Exception):
    pass


class ConversationNotFoundError(ConversationServiceError):
    pass


# ---------------- codex OAuth (service layer) ----------------

class CodexOAuthServiceError(Exception):
    pass


class CodexOAuthUnknownFlowError(CodexOAuthServiceError):
    pass


class CodexOAuthUpstreamError(CodexOAuthServiceError):
    pass


# ---------------- codex credentials ----------------

class CodexCredentialError(Exception):
    """Raised when a stored Codex connection exists but refresh fails."""


# ---------------- credentials (generic) ----------------

class CredentialServiceError(Exception):
    """Credential lookup or refresh failed."""


# ---------------- exports ----------------

class ExportServiceError(Exception):
    pass


class ExportNotFoundError(ExportServiceError):
    pass


# ---------------- google OAuth ----------------

class GoogleOAuthServiceError(Exception):
    """Base class for Google OAuth flow failures.

    Route code catches this and redirects to `/?oauth_error=<reason>` so
    the frontend can surface a banner. Messages are safe-to-show to users.
    """


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


# ---------------- settings ----------------

class SettingsServiceError(Exception):
    pass


class SettingsNotFoundError(SettingsServiceError):
    pass
