"""Application service for the Database browser's helper chat.

Stateless: takes a fresh message list each turn, runs the agent loop in
`backend/domain/agent/stateless.py`, streams events back. Writes nothing
to the runtime store — refresh wipes the conversation by design.

Shape mirrors `ChatService`: `prepare(...)` resolves the provider/client
up front (so credential failures surface to the route before the SSE
body starts), and `stream_events(prepared, ...)` returns the runtime
event source. The route layer owns acquiring the concurrency slot and
closing the client in a `finally` block — same pattern as `/chat/stream`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncGenerator

from backend.application.oauth.provider_credentials import (
    CredentialServiceError,
    ProviderCredentialService,
)
from backend.domain.agent.events import RuntimeEvent
from backend.domain.agent.message_builder import build_messages_from_raw
from backend.domain.agent.stateless import (
    ALLOWED_HELPER_TOOLS,
    run_stateless_turn,
)
from backend.domain.agent.system_prompt import get_db_helper_prompt
from backend.domain.auth.types import AuthenticatedUser
from backend.domain.providers.base import BaseLLMClient
from backend.domain.providers.types import ToolChoice
from backend.domain.tools import TOOLS
from backend.data import RuntimeStore
from backend.runtime_state import PerUserLockRegistry


class HelperChatServiceError(Exception):
    """Base class for helper-chat application failures."""


class HelperChatConfigurationError(HelperChatServiceError):
    """Provider, credential, or model selection failed."""


@dataclass
class PreparedHelperChat:
    """Return shape for `DbHelperChatService.prepare`. The route owns
    client cleanup once it gets one back, mirroring `PreparedChat`."""

    client: BaseLLMClient
    provider_name: str


_HELPER_TOOLS = [t for t in TOOLS if t.name in ALLOWED_HELPER_TOOLS]


@dataclass
class DbHelperChatService:
    """Runs one turn of the Database helper chat without persistence."""

    store: RuntimeStore
    refresh_locks: PerUserLockRegistry

    def __post_init__(self) -> None:
        # Composed sub-service — same pattern as ChatService.
        self.credentials = ProviderCredentialService(self.store, self.refresh_locks)

    async def prepare(
        self,
        *,
        provider: str | None,
        model: str | None,
        user: AuthenticatedUser,
    ) -> PreparedHelperChat:
        try:
            resolved = await self.credentials.resolve_provider_client(
                user_id=user.id, role=user.role, provider=provider, model=model
            )
        except CredentialServiceError as exc:
            raise HelperChatConfigurationError(str(exc)) from exc
        return PreparedHelperChat(
            client=resolved.client, provider_name=resolved.provider_name
        )

    def stream_events(
        self,
        prepared: PreparedHelperChat,
        *,
        messages: list[dict],
        tool_choice: ToolChoice | None,
    ) -> AsyncGenerator[RuntimeEvent, None]:
        """Return the runtime event source for one helper-chat turn.

        The caller iterates the generator and is responsible for closing
        `prepared.client` in its own `finally`. `aclose()`-ing the
        returned generator is also safe — the inner stateless loop has
        no resources to release beyond the LLM client itself.
        """
        wire_messages = build_messages_from_raw(messages)
        system = get_db_helper_prompt()
        return run_stateless_turn(
            messages=wire_messages,
            client=prepared.client,
            tools=_HELPER_TOOLS,
            system=system,
            tool_choice=tool_choice,
        )
