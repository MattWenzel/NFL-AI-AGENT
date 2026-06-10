"""Exceptions raised by the storage layer."""

from __future__ import annotations


class IdentityConflictError(Exception):
    """Raised when attempting to create an identity that already exists for
    a different user — the caller should surface this to the user (e.g.
    "this Google account is already linked to another user")."""


class ExportLimitExceededError(Exception):
    """Raised when registering a CSV export would push a user past
    MAX_EXPORTS_PER_USER. Callers should surface the message and let the
    user delete old exports to free slots."""
