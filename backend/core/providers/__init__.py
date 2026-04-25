"""LLM provider registry and factory."""

import os
import logging
from dataclasses import dataclass, field

from backend.core.providers.base import BaseLLMClient
from backend.core.providers.errors import ContextOverflowError, LLMError
from backend.core.providers.types import (
    ANTHROPIC, CODEX, OPENAI, CredentialShape, Message, MessageResponse,
    RetryingEvent, StopReason, TextEvent, ToolChoice, ToolDefinition,
    ToolUseEvent, Usage,
)

logger = logging.getLogger(__name__)


@dataclass
class ProviderInfo:
    """Metadata about a registered LLM provider."""
    name: str
    display_name: str
    env_key: str
    default_model: str
    # Cheap/fast sibling model used for one-shot compaction summaries. When
    # None, the session's active model is reused. Compaction runs once per
    # long conversation, so picking a smaller model here keeps the per-
    # session cost of summarization in the noise.
    summarizer_model: str | None = None
    models: list[str] = field(default_factory=list)
    context_window: int = 128_000
    max_output_tokens: int = 16384
    supports_streaming: bool = True
    supports_tools: bool = True
    client_class: type[BaseLLMClient] | None = None
    # How the user supplies this provider's credential. "api_key" is the
    # default (paste a string in Settings); "codex_oauth" replaces the
    # paste field with a "Connect ChatGPT" device-code flow.
    credential_shape: CredentialShape = "api_key"

    @property
    def effective_context_window(self) -> int:
        """75% of max context window — budget for conversation history."""
        return self.context_window // 4 * 3


_registry: dict[str, ProviderInfo] = {}
_BUILTINS_REGISTERED = False


def register_provider(info: ProviderInfo) -> None:
    """Register a provider."""
    _registry[info.name] = info
    logger.debug("Registered LLM provider: %s", info.name)


def get_provider(name: str) -> ProviderInfo:
    """Get a registered provider by name. Raises KeyError if not found."""
    ensure_builtin_providers_registered()
    if name not in _registry:
        available = ", ".join(sorted(_registry.keys()))
        raise KeyError(f"Unknown provider '{name}'. Available: {available}")
    return _registry[name]


def list_providers() -> list[ProviderInfo]:
    """List all registered providers."""
    ensure_builtin_providers_registered()
    return list(_registry.values())


def get_default_provider() -> str:
    """Get the default provider name from env or fallback to Anthropic."""
    return os.environ.get("CHAT_PROVIDER", ANTHROPIC)


def create_client(
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> BaseLLMClient:
    """Factory: create an LLM client for the given provider."""
    ensure_builtin_providers_registered()
    provider_name = provider or get_default_provider()
    info = get_provider(provider_name)

    if info.client_class is None:
        raise LLMError(f"Provider '{provider_name}' has no client class registered")

    resolved_model = model or info.default_model
    env_value = os.environ.get(info.env_key) if info.env_key else None
    resolved_key = api_key or env_value
    if not resolved_key:
        if info.credential_shape == "codex_oauth":
            raise LLMError(
                f"{info.display_name} not connected — click Connect ChatGPT in Settings."
            )
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
    """Whether the provider's default credential source is set in the
    process environment. OAuth-only providers always return False — they
    require per-user credentials regardless of env configuration."""
    if info.credential_shape == "codex_oauth":
        return False
    return bool(info.env_key and os.environ.get(info.env_key))


def ensure_builtin_providers_registered() -> None:
    """Register built-in providers exactly once."""
    global _BUILTINS_REGISTERED
    if _BUILTINS_REGISTERED:
        return

    from backend.core.providers.clients.anthropic import AnthropicClient  # noqa: E402

    register_provider(ProviderInfo(
        name=ANTHROPIC,
        display_name="Anthropic",
        env_key="ANTHROPIC_API_KEY",
        default_model="claude-sonnet-4-6",
        summarizer_model="claude-haiku-4-5-20251001",
        models=[
            "claude-opus-4-7",
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-sonnet-4-20250514",
            "claude-haiku-4-5-20251001",
        ],
        context_window=200_000,
        max_output_tokens=64_000,
        supports_streaming=True,
        supports_tools=True,
        client_class=AnthropicClient,
    ))

    try:
        from backend.core.providers.clients.openai import OpenAIClient  # noqa: E402
        register_provider(ProviderInfo(
            name=OPENAI,
            display_name="OpenAI",
            env_key="OPENAI_API_KEY",
            default_model="gpt-5",
            summarizer_model="gpt-5-mini",
            models=[
                "gpt-5",
                "gpt-5-mini",
                "gpt-4.1",
                "gpt-4.1-mini",
                "gpt-4o",
                "gpt-4o-mini",
                "o3",
                "o3-mini",
            ],
            context_window=128_000,
            max_output_tokens=16384,
            supports_streaming=True,
            supports_tools=True,
            client_class=OpenAIClient,
        ))
    except ImportError:
        logger.debug("OpenAI SDK not installed — openai provider unavailable")

    from backend.core.providers.clients.codex import OpenAICodexClient  # noqa: E402

    register_provider(ProviderInfo(
        name=CODEX,
        display_name="OpenAI Codex (ChatGPT)",
        env_key="",
        default_model="gpt-5.3-codex",
        summarizer_model="gpt-5.3-codex",
        models=["gpt-5.3-codex"],
        context_window=200_000,
        max_output_tokens=16384,
        supports_streaming=True,
        supports_tools=True,
        client_class=OpenAICodexClient,
        credential_shape="codex_oauth",
    ))
    _BUILTINS_REGISTERED = True

__all__ = [
    "BaseLLMClient", "ContextOverflowError", "CredentialShape", "LLMError",
    "Message", "MessageResponse", "RetryingEvent", "TextEvent", "ToolUseEvent",
    "ProviderInfo", "StopReason", "ToolChoice", "Usage", "ToolDefinition",
    "register_provider", "get_provider", "list_providers",
    "get_default_provider", "create_client", "provider_is_available",
]
