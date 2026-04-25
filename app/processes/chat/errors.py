"""Chat process service errors."""

from __future__ import annotations


class ChatServiceError(Exception):
    """Base class for application-service chat failures."""


class ChatNotFoundError(ChatServiceError):
    """The referenced conversation does not exist for the caller."""


class ChatConfigurationError(ChatServiceError):
    """Provider, credential, or model selection failed."""
