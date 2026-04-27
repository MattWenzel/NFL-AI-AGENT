"""Application service for the Database browser's helper chat.

Stateless: takes a fresh message list each turn, runs the agent loop in
`backend/domain/agent/stateless.py`, streams events back. Writes nothing
to the runtime store — refresh wipes the conversation by design.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncGenerator

from backend.application.chat import close_client
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
from backend.domain.providers import (
    create_client,
    get_default_provider,
    get_provider,
    provider_is_available,
)
from backend.domain.providers.errors import LLMError
from backend.domain.providers.types import ToolChoice
from backend.domain.tools import TOOLS
from backend.data import RuntimeStore
from backend.runtime_state import PerUserLockRegistry


class DbHelperChatError(Exception):
    """Base class for helper-chat preparation failures."""


class DbHelperChatConfigurationError(DbHelperChatError):
    """Provider/credential resolution failed."""


_HELPER_TOOLS = [t for t in TOOLS if t.name in ALLOWED_HELPER_TOOLS]


@dataclass
class DbHelperChatService:
    """Runs one turn of the Database helper chat without persistence."""

    store: RuntimeStore
    refresh_locks: PerUserLockRegistry

    def __post_init__(self) -> None:
        self.credentials = ProviderCredentialService(self.store, self.refresh_locks)

    async def stream(
        self,
        *,
        messages: list[dict],
        provider: str | None,
        model: str | None,
        tool_choice: ToolChoice | None,
        user: AuthenticatedUser,
    ) -> AsyncGenerator[RuntimeEvent, None]:
        """Stream a helper-chat turn.

        Resolves the provider + API key the same way `ChatService.prepare_chat`
        does, then runs the stateless loop. The route layer iterates the
        returned generator and serializes each event as SSE.
        """
        provider_name = provider or get_default_provider()
        try:
            info = get_provider(provider_name)
        except KeyError as exc:
            raise DbHelperChatConfigurationError(str(exc)) from exc

        try:
            user_key = await self.credentials.get_api_key(
                user_id=user.id,
                provider_name=provider_name,
            )
        except CredentialServiceError as exc:
            raise DbHelperChatConfigurationError(str(exc)) from exc

        if not user_key and not provider_is_available(info):
            if info.credential_shape == "codex_oauth":
                raise DbHelperChatConfigurationError(
                    f"{info.display_name} not connected — click Connect ChatGPT in Settings."
                )
            raise DbHelperChatConfigurationError(
                f"No API key for {info.display_name} — add one in Settings."
            )

        try:
            client = create_client(
                provider=provider_name, model=model, api_key=user_key
            )
        except LLMError as exc:
            raise DbHelperChatConfigurationError(str(exc)) from exc

        wire_messages = build_messages_from_raw(messages)
        system = get_db_helper_prompt()
        try:
            async for event in run_stateless_turn(
                messages=wire_messages,
                client=client,
                tools=_HELPER_TOOLS,
                system=system,
                tool_choice=tool_choice,
            ):
                yield event
        finally:
            await close_client(client)
