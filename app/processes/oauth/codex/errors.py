"""Codex OAuth process service errors."""

from __future__ import annotations


class CodexOAuthServiceError(Exception):
    pass


class CodexOAuthUnknownFlowError(CodexOAuthServiceError):
    pass


class CodexOAuthUpstreamError(CodexOAuthServiceError):
    pass


class CodexCredentialError(Exception):
    """Raised when a stored Codex connection exists but refresh fails."""
