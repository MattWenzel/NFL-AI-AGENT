"""Exceptions raised by the storage layer."""

from __future__ import annotations


class IdentityConflictError(Exception):
    """Raised when attempting to create an identity that already exists for
    a different user — the caller should surface this to the user (e.g.
    "this Google account is already linked to another user")."""
