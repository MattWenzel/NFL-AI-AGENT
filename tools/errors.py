"""Exceptions raised by tool handlers."""

from __future__ import annotations


class SQLValidationError(Exception):
    """Raised when SQL fails validation checks (read-only, single-statement)."""
