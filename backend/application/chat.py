"""Chat application service: request preparation + streaming orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncGenerator

from backend.domain.auth.types import AuthenticatedUser
from backend.domain.agent.events import RuntimeEvent
from backend.domain.providers.base import BaseLLMClient
from backend.domain.providers.errors import LLMError
from backend.domain.agent.runtime import ChatRuntime
from backend.domain.providers.types import ToolChoice
from backend.domain.tools import TOOLS
from backend.data import RuntimeStore, SessionRecord
from backend.application.oauth.provider_credentials import (
    CredentialServiceError,
    ProviderCredentialService,
)
from backend.runtime_state import PerUserLockRegistry


_TABLE_RESEARCH_TOOLS: frozenset[str] = frozenset(
    {"search_players", "get_player_info", "execute_sql", "get_guide", "get_schema"}
)


class ChatServiceError(Exception):
    """Base class for application-service chat failures."""


class ChatNotFoundError(ChatServiceError):
    """The referenced conversation does not exist for the caller."""


class ChatConfigurationError(ChatServiceError):
    """Provider, credential, or model selection failed."""


@dataclass
class PreparedChat:
    """Return shape for `ChatService.prepare_chat`. Carries the LLM client,
    the resolved provider, and the session record so the route layer can
    drive the stream and own client cleanup."""
    client: BaseLLMClient
    provider_name: str
    session: SessionRecord


async def close_client(client: BaseLLMClient) -> None:
    try:
        await client.aclose()
    except (LLMError, OSError, RuntimeError):
        # Callers already have request-context logging.
        pass


@dataclass
class ChatService:
    """Owns chat request preparation and the runtime event-stream wiring."""

    runtime: ChatRuntime
    store: RuntimeStore
    refresh_locks: PerUserLockRegistry

    def __post_init__(self) -> None:
        # Composed sub-service — derived from injected store + refresh_locks,
        # not itself injected. Keeping it on `self` avoids re-instantiating
        # per request.
        self.credentials = ProviderCredentialService(self.store, self.refresh_locks)

    async def prepare_chat(
        self,
        *,
        conversation_id: str | None,
        provider: str | None,
        model: str | None,
        user: AuthenticatedUser,
    ) -> PreparedChat:
        if (
            conversation_id
            and await self.store.get_session(conversation_id, user_id=user.id) is None
        ):
            raise ChatNotFoundError("Conversation not found")

        try:
            resolved = await self.credentials.resolve_provider_client(
                user_id=user.id, role=user.role, provider=provider, model=model
            )
        except CredentialServiceError as exc:
            raise ChatConfigurationError(str(exc)) from exc

        try:
            session = await self.runtime.prepare_session(
                resolved.client,
                resolved.provider_name,
                conversation_id,
                user_id=user.id,
            )
        except Exception:
            await close_client(resolved.client)
            raise
        return PreparedChat(
            client=resolved.client,
            provider_name=resolved.provider_name,
            session=session,
        )

    def stream_events(
        self,
        prepared: PreparedChat,
        *,
        message: str,
        tool_choice: ToolChoice | None,
    ) -> AsyncGenerator[RuntimeEvent, None]:
        """Return the runtime event source for a prepared chat turn.

        Wraps `ChatRuntime.run_session` so route code can drive the stream
        without knowing the service holds a runtime. Callers can both
        iterate and `aclose()` to force the runtime's `finally` block —
        releases the session lock, reconciles pending tool runs — rather
        than waiting on GC. Callers also own closing `prepared.client`.
        """
        tools = self._tools_for_session(prepared.session)
        return self.runtime.run_session(
            prepared.session,
            message,
            prepared.client,
            tools=tools,
            provider_name=prepared.provider_name,
            tool_choice=tool_choice,
        )

    @staticmethod
    def _tools_for_session(session: SessionRecord) -> list:
        """Whitelist tools based on session kind.

        - regular chat → all TOOLS minus `set_table`
        - table_chat   → research tools + `set_table`

        Inside a Report we drop the content-creation tools (`create_report`,
        `create_chart`, `create_csv_export`) — the user is already viewing
        tabular data, so spawning another Report or a chart is off-task.
        Whether `set_table` actually mutates is decided per-call by the
        handler reading the table's `locked` flag.
        """
        if session.kind == "table_chat":
            return [
                t for t in TOOLS
                if t.name in _TABLE_RESEARCH_TOOLS or t.name == "set_table"
            ]
        return [t for t in TOOLS if t.name != "set_table"]
