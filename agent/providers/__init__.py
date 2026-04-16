"""LLM provider registry and factory."""

import os
import logging
from dataclasses import dataclass, field
from typing import Literal

from agent.providers.base import (
    BaseLLMClient, LLMError, Message, MessageResponse, TextEvent, ToolUseEvent,
    StopReason, Usage, ToolDefinition,
)

logger = logging.getLogger(__name__)


AuthType = Literal["api_key", "oauth"]


@dataclass
class ProviderInfo:
    """Metadata about a registered LLM provider."""
    name: str
    display_name: str
    env_key: str | None  # None for OAuth providers (they authenticate on disk)
    default_model: str
    models: list[str] = field(default_factory=list)
    context_window: int = 128_000
    max_output_tokens: int = 4096
    supports_streaming: bool = True
    supports_tools: bool = True
    client_class: type[BaseLLMClient] | None = None
    auth_type: AuthType = "api_key"

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


def _oauth_token_store():
    """Return the on-disk OAuth TokenStore used by OAuth providers.

    Imported lazily so the oauth package (and httpx) is only pulled in when an
    OAuth provider is actually consulted.
    """
    from agent.oauth.token_store import TokenStore
    from config import CODEX_AUTH_PATH

    return TokenStore(CODEX_AUTH_PATH)


def _build_oauth_client(info: "ProviderInfo", resolved_model: str) -> BaseLLMClient:
    from agent.oauth.codex_auth import CodexAuth

    store = _oauth_token_store()
    if not store.has_record():
        raise LLMError(
            f"{info.display_name} not authenticated. "
            f"Run `python3 chat_cli.py login --provider {info.name}` to sign in."
        )
    return info.client_class(
        model=resolved_model,
        max_output_tokens=info.max_output_tokens,
        auth=CodexAuth(store),
    )


def create_client(
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> BaseLLMClient:
    """Factory: create an LLM client for the given provider.

    Args:
        provider: Provider name (defaults to CHAT_PROVIDER env var or 'anthropic')
        model: Model name (defaults to provider's default)
        api_key: API key (ignored for OAuth providers; defaults to provider's env var)
    """
    provider_name = provider or get_default_provider()
    info = get_provider(provider_name)

    if info.client_class is None:
        raise LLMError(f"Provider '{provider_name}' has no client class registered")

    resolved_model = model or info.default_model

    if info.auth_type == "oauth":
        return _build_oauth_client(info, resolved_model)

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
    """Whether the provider has everything it needs to construct a client now.

    For api-key providers that's the env var; for OAuth providers that's an
    on-disk token record. Callers use this to populate the `available` flag in
    the `/chat/providers` response.
    """
    if info.auth_type == "oauth":
        return _oauth_token_store().has_record()
    return bool(os.environ.get(info.env_key))


# Register providers on import
from agent.providers.anthropic_provider import AnthropicClient  # noqa: E402

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

# Lazy-loaded providers (only if SDK is installed)
try:
    from agent.providers.openai_provider import OpenAIClient  # noqa: E402
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

try:
    from agent.providers.codex_provider import CodexClient  # noqa: E402
    register_provider(ProviderInfo(
        name="codex",
        display_name="ChatGPT Codex",
        env_key=None,  # OAuth provider — authenticates via on-disk token store
        default_model="gpt-5.1-codex",
        models=["gpt-5.1-codex", "gpt-5.3-codex"],
        context_window=200_000,
        max_output_tokens=8192,
        supports_streaming=True,
        supports_tools=True,
        client_class=CodexClient,
        auth_type="oauth",
    ))
except ImportError:
    logger.debug("httpx not installed — codex provider unavailable")

__all__ = [
    "BaseLLMClient", "LLMError", "Message", "MessageResponse",
    "TextEvent", "ToolUseEvent", "ProviderInfo",
    "StopReason", "Usage", "ToolDefinition",
    "register_provider", "get_provider", "list_providers",
    "get_default_provider", "create_client", "provider_is_available",
]
