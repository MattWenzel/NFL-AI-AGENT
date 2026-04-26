"""LLM provider registry and factory — public surface."""

from backend.domain.providers.base import BaseLLMClient
from backend.domain.providers.errors import ContextOverflowError, LLMError
from backend.domain.providers.registry import (
    ProviderInfo,
    create_client,
    get_default_provider,
    get_provider,
    list_providers,
    provider_is_available,
    register_provider,
)
from backend.domain.providers.types import (
    CredentialShape,
    Message,
    MessageResponse,
    ProviderRetryingEvent,
    StopReason,
    TextEvent,
    ToolChoice,
    ToolDefinition,
    ToolUseEvent,
    Usage,
)

__all__ = [
    "BaseLLMClient",
    "ContextOverflowError",
    "CredentialShape",
    "LLMError",
    "Message",
    "MessageResponse",
    "ProviderInfo",
    "ProviderRetryingEvent",
    "StopReason",
    "TextEvent",
    "ToolChoice",
    "ToolDefinition",
    "ToolUseEvent",
    "Usage",
    "create_client",
    "get_default_provider",
    "get_provider",
    "list_providers",
    "provider_is_available",
    "register_provider",
]
