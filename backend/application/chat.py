"""Chat application service: request preparation + response aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncGenerator

from backend.domain.auth.types import AuthenticatedUser
from backend.domain.agent.events import (
    RuntimeErrorEvent,
    RuntimeEvent,
    TextDeltaEvent,
    ToolCompletedEvent,
    ToolFailedEvent,
    ToolPendingEvent,
)
from backend.domain.providers.base import BaseLLMClient
from backend.domain.providers.errors import LLMError
from backend.domain.agent.runtime import ChatRuntime
from backend.domain.providers import (
    create_client,
    get_default_provider,
    get_provider,
    provider_is_available,
)
from backend.domain.providers.types import ToolChoice
from backend.domain.tools import TOOLS
from backend.data import RuntimeStore, SessionRecord
from backend.application.oauth.provider_credentials import (
    CredentialServiceError,
    ProviderCredentialService,
)
from backend.runtime_state import PerUserLockRegistry


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


@dataclass
class _ToolCallLogEntry:
    """Internal — accumulates tool-call previews while aggregating a turn's
    response in `ChatService.run_message`. Not part of the public surface."""
    tool_run_id: str
    tool: str
    input: dict
    result_preview: str = ""


async def close_client(client: BaseLLMClient) -> None:
    try:
        await client.aclose()
    except (LLMError, OSError, RuntimeError):
        # Callers already have request-context logging.
        pass


@dataclass
class ChatService:
    """Owns chat request preparation and non-streaming response aggregation."""

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

        provider_name = provider or get_default_provider()
        try:
            info = get_provider(provider_name)
        except KeyError as exc:
            raise ChatConfigurationError(str(exc)) from exc

        try:
            user_key = await self.credentials.get_api_key(
                user_id=user.id,
                provider_name=provider_name,
            )
        except CredentialServiceError as exc:
            raise ChatConfigurationError(str(exc)) from exc

        if not user_key and not provider_is_available(info):
            if info.credential_shape == "codex_oauth":
                raise ChatConfigurationError(
                    f"{info.display_name} not connected — click Connect ChatGPT in Settings."
                )
            raise ChatConfigurationError(
                f"No API key for {info.display_name} — add one in Settings."
            )

        try:
            client = create_client(provider=provider_name, model=model, api_key=user_key)
        except LLMError as exc:
            raise ChatConfigurationError(str(exc)) from exc

        try:
            session = await self.runtime.prepare_session(
                client,
                provider_name,
                conversation_id,
                user_id=user.id,
            )
        except Exception:
            await close_client(client)
            raise
        return PreparedChat(client=client, provider_name=provider_name, session=session)

    def stream_events(
        self,
        prepared: PreparedChat,
        *,
        message: str,
        tool_choice: ToolChoice | None,
        table_mode: str | None = None,
        table_max_rows: int | None = None,
    ) -> AsyncGenerator[RuntimeEvent, None]:
        """Return the runtime event source for a prepared chat turn.

        Wraps `ChatRuntime.run_session` so route code can drive the stream
        without knowing the service holds a runtime. The return type is an
        async generator — callers can both iterate and `aclose()` to force
        the runtime's `finally` block (releases the session lock, reconciles
        pending tool runs) rather than waiting on GC. Callers also own
        closing `prepared.client`.

        For table-view chats, `table_mode` selects which tools the agent
        sees this turn (explore = research-only, edit_table = set_table only),
        and `table_max_rows` is forwarded to the `set_table` tool ctx as
        the row cap chosen via the composer's size dropdown.
        """
        effective_mode = self._resolve_table_mode(prepared.session, table_mode)
        tools = self._tools_for_mode(effective_mode)
        return self.runtime.run_session(
            prepared.session,
            message,
            prepared.client,
            tools=tools,
            provider_name=prepared.provider_name,
            tool_choice=tool_choice,
            table_mode=effective_mode,
            table_max_rows=table_max_rows,
        )

    @staticmethod
    def _resolve_table_mode(
        session: SessionRecord, requested_mode: str | None
    ) -> str | None:
        """Pick the effective table mode for this turn.

        Regular chats (`session.kind == "chat"`) ignore `requested_mode` —
        set_table is never exposed and `get_base_prompt()` runs without
        the table addendum. Table-view chats default to `explore` if the
        client didn't send a mode.
        """
        if session.kind != "table_chat":
            return None
        if requested_mode in ("explore", "edit_table"):
            return requested_mode
        return "explore"

    @staticmethod
    def _tools_for_mode(table_mode: str | None) -> list:
        """Whitelist tools for the agent based on table mode.

        - regular chat        → all TOOLS minus `set_table`
        - table chat: explore → all TOOLS minus `set_table`
        - table chat: edit    → only `set_table`
        """
        if table_mode == "edit_table":
            return [t for t in TOOLS if t.name == "set_table"]
        return [t for t in TOOLS if t.name != "set_table"]

    async def run_message(
        self,
        *,
        message: str,
        conversation_id: str | None,
        provider: str | None,
        model: str | None,
        tool_choice: ToolChoice | None,
        user: AuthenticatedUser,
        table_mode: str | None = None,
        table_max_rows: int | None = None,
    ) -> dict:
        prepared = await self.prepare_chat(
            conversation_id=conversation_id,
            provider=provider,
            model=model,
            user=user,
        )
        response_text = ""
        tool_calls_log: list[_ToolCallLogEntry] = []
        hit_limit = False
        runtime_error = None

        try:
            async for event in self.stream_events(
                prepared,
                message=message,
                tool_choice=tool_choice,
                table_mode=table_mode,
                table_max_rows=table_max_rows,
            ):
                if isinstance(event, TextDeltaEvent) and event.text:
                    response_text += event.text
                elif isinstance(event, ToolPendingEvent):
                    tool_calls_log.append(
                        _ToolCallLogEntry(
                            tool_run_id=event.tool_run_id,
                            tool=event.name,
                            input=event.input,
                        )
                    )
                elif isinstance(event, (ToolCompletedEvent, ToolFailedEvent)) and tool_calls_log:
                    preview = (
                        event.result[:500]
                        if event.result and len(event.result) > 500
                        else event.result or event.error or ""
                    )
                    for item in reversed(tool_calls_log):
                        if item.tool_run_id == event.tool_run_id and not item.result_preview:
                            item.result_preview = preview
                            break
                elif isinstance(event, RuntimeErrorEvent):
                    runtime_error = event.error or "Runtime error"
                    hit_limit = bool(runtime_error.startswith("Reached maximum tool iterations"))
            if runtime_error and not hit_limit:
                raise ChatServiceError(runtime_error)
            return {
                "conversation_id": prepared.session.id,
                "response": response_text,
                "tool_calls": [
                    {
                        "tool": item.tool,
                        "input": item.input,
                        "result_preview": item.result_preview,
                    }
                    for item in tool_calls_log
                ],
                "truncated": hit_limit,
            }
        finally:
            await close_client(prepared.client)
