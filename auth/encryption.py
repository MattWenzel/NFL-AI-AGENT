"""Symmetric encryption for user-supplied secrets (API keys, etc.).

Uses Fernet (AES-128-CBC + HMAC-SHA256) with a master key loaded from the
`SETTINGS_ENCRYPTION_KEY` env var. The key never leaves this module; callers
just ask for encrypt/decrypt.

To swap to KMS/Vault later, replace the internals of `encrypt` / `decrypt`
and keep the same signatures. Ciphertext format stays str → str.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

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


_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Lazy-load the Fernet instance so env-var-less contexts (CLI, tests) don't break on import."""
    global _fernet
    if _fernet is not None:
        return _fernet
    raw = os.environ.get(ENV_KEY, "").strip()
    if not raw:
        raise EncryptionKeyMissing()
    try:
        _fernet = Fernet(raw.encode())
    except (ValueError, TypeError) as exc:
        raise EncryptionKeyInvalid(exc) from exc
    return _fernet


def reset_cache() -> None:
    """Drop the cached Fernet instance. Test helper — lets tests rotate the env var between cases."""
    global _fernet
    _fernet = None


def require_configured() -> None:
    """Validate the encryption key at startup. Raises if missing or malformed."""
    _get_fernet()


def encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Ciphertext failed authentication — wrong key or tampered value") from exc
