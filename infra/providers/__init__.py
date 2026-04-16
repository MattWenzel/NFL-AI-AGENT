"""LLM provider registry and factory."""

import os
import logging
from dataclasses import dataclass, field

from infra.providers.base import (
    BaseLLMClient, LLMError, Message, MessageResponse, TextEvent, ToolUseEvent,
    StopReason, Usage, ToolDefinition,
)

logger = logging.getLogger(__name__)


@dataclass
class ProviderInfo:
    """Metadata about a registered LLM provider."""
    name: str
    display_name: str
    env_key: str
    default_model: str
    models: list[str] = field(default_factory=list)
    context_window: int = 128_000
    max_output_tokens: int = 4096
    supports_streaming: bool = True
    supports_tools: bool = True
    client_class: type[BaseLLMClient] | None = None

    @property
    def effective_context_window(self) -> int:
        """75% of max context window — budget for conversation history."""
        return self.context_window // 4 * 3


_registry: dict[str, ProviderInfo] = {}


def register_provider(info: ProviderInfo) -> None:
    """Register a provider."""
    _registry[info.name] = info
    logger.debug("Registered LLM provider: %s", info.name)


def get_provider(name: str) -> ProviderInfo:
    """Get a registered provider by name. Raises KeyError if not found."""
    if name not in _registry:
        available = ", ".join(sorted(_registry.keys()))
        raise KeyError(f"Unknown provider '{name}'. Available: {available}")
    return _registry[name]


def list_providers() -> list[ProviderInfo]:
    """List all registered providers."""
    return list(_registry.values())


def get_default_provider() -> str:
    """Get the default provider name from env or fallback to 'anthropic'."""
    return os.environ.get("CHAT_PROVIDER", "anthropic")


def create_client(
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> BaseLLMClient:
    """Factory: create an LLM client for the given provider."""
    provider_name = provider or get_default_provider()
    info = get_provider(provider_name)

    if info.client_class is None:
        raise LLMError(f"Provider '{provider_name}' has no client class registered")

    resolved_model = model or info.default_model
    resolved_key = api_key or os.environ.get(info.env_key)
    if not resolved_key:
        raise LLMError(
            f"{info.display_name} API key not set. "
            f"Set {info.env_key} environment variable or pass api_key parameter."
        )
    return info.client_class(
        model=resolved_model,
        max_output_tokens=info.max_output_tokens,
        api_key=resolved_key,
    )


def provider_is_available(info: "ProviderInfo") -> bool:
    """Whether the provider's API key is set."""
    return bool(os.environ.get(info.env_key))


# Register providers on import
from infra.providers.anthropic import AnthropicClient  # noqa: E402

register_provider(ProviderInfo(
    name="anthropic",
    display_name="Anthropic",
    env_key="ANTHROPIC_API_KEY",
    default_model="claude-sonnet-4-20250514",
    models=[
        "claude-sonnet-4-20250514",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-6",
    ],
    context_window=200_000,
    max_output_tokens=4096,
    supports_streaming=True,
    supports_tools=True,
    client_class=AnthropicClient,
))

try:
    from infra.providers.openai import OpenAIClient  # noqa: E402
    register_provider(ProviderInfo(
        name="openai",
        display_name="OpenAI",
        env_key="OPENAI_API_KEY",
        default_model="gpt-4o",
        models=["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o3-mini"],
        context_window=128_000,
        max_output_tokens=4096,
        supports_streaming=True,
        supports_tools=True,
        client_class=OpenAIClient,
    ))
except ImportError:
    logger.debug("OpenAI SDK not installed — openai provider unavailable")

__all__ = [
    "BaseLLMClient", "LLMError", "Message", "MessageResponse",
    "TextEvent", "ToolUseEvent", "ProviderInfo",
    "StopReason", "Usage", "ToolDefinition",
    "register_provider", "get_provider", "list_providers",
    "get_default_provider", "create_client", "provider_is_available",
]
