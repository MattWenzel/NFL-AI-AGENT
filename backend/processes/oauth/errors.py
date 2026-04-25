"""Shared OAuth credential service errors."""

from __future__ import annotations


class CredentialServiceError(Exception):
    """Credential lookup or refresh failed."""
