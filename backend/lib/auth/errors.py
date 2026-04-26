"""Exceptions raised by the auth subsystem.

Grouped here so every auth-related failure mode — password/session flows,
OAuth protocols, encryption, email — has one predictable import path.
"""

from __future__ import annotations


# ---------------- Account lifecycle ----------------

class AuthConflictError(Exception):
    """Raised when account creation collides with an existing email."""


# ---------------- Codex (ChatGPT) OAuth ----------------

class CodexOAuthError(Exception):
    """User-facing error raised from any Codex OAuth helper."""


class DeviceCodeExpired(CodexOAuthError):
    """The 15-minute device-code window elapsed without sign-in."""


# ---------------- Google OAuth ----------------

class GoogleOAuthError(Exception):
    """Raised for any failure in the OAuth exchange or ID-token verification.

    The message is safe to show a developer; user-facing errors should be
    generic ("Sign-in failed — try again") so we don't leak protocol detail.
    """


# ---------------- Email ----------------

class EmailError(Exception):
    """Raised on a non-retryable send failure the caller should surface."""


# ---------------- Encryption ----------------

ENV_KEY = "SETTINGS_ENCRYPTION_KEY"

_GENERATE_HINT = (
    "Generate one with: python3 -c "
    "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
)


class EncryptionKeyMissing(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            f"{ENV_KEY} is not set. {_GENERATE_HINT}"
        )


class EncryptionKeyInvalid(RuntimeError):
    def __init__(self, cause: Exception) -> None:
        super().__init__(
            f"{ENV_KEY} is set but not a valid Fernet key ({cause}). {_GENERATE_HINT}"
        )
