"""Conversation process service errors."""

from __future__ import annotations


class ConversationServiceError(Exception):
    pass


class ConversationNotFoundError(ConversationServiceError):
    pass
