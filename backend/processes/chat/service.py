"""Application service for chat request orchestration."""

from __future__ import annotations

from typing import AsyncGenerator

from backend.security.types import AuthenticatedUser
from backend.agent.events import (
    RuntimeErrorEvent,
    RuntimeEvent,
    TextDeltaEvent,
    ToolCompletedEvent,
    ToolFailedEvent,
    ToolPendingEvent,
)
from backend.providers.base import BaseLLMClient
from backend.providers.errors import LLMError
from backend.agent.runtime import ChatRuntime
from backend.providers import (
    create_client,
    get_default_provider,
    get_provider,
    provider_is_available,
)
from backend.persistence import RuntimeStore
from backend.processes.chat.schemas import ChatRequest, ChatResponse, ToolCallPreview
from backend.processes.oauth.credentials import (
    CredentialServiceError,
    ProviderCredentialService,
)
from backend.processes.chat.types import PreparedChat, ToolCallLogEntry
from backend.runtime_state import PerUserLockRegistry


class ChatServiceError(Exception):
    """Base class for application-service chat failures."""


class ChatNotFoundError(ChatServiceError):
    """The referenced conversation does not exist for the caller."""


class ChatConfigurationError(ChatServiceError):
    """Provider, credential, or model selection failed."""


async def close_client(client: BaseLLMClient) -> None:
    try:
        await client.aclose()
    except (LLMError, OSError, RuntimeError):
        # Callers already have request-context logging.
        pass


class ChatService:
    """Owns chat request preparation and non-streaming response aggregation."""

    def __init__(
        self,
        runtime: ChatRuntime,
        store: RuntimeStore,
        *,
        refresh_locks: PerUserLockRegistry,
    ):
        self.runtime = runtime
        self.store = store
        self.credentials = ProviderCredentialService(store, refresh_locks)

    async def prepare_chat(
        self,
        body: ChatRequest,
        user: AuthenticatedUser,
    ) -> PreparedChat:
        if (
            body.conversation_id
            and await self.store.get_session(body.conversation_id, user_id=user.id) is None
        ):
            raise ChatNotFoundError("Conversation not found")

        provider_name = body.provider or get_default_provider()
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
            client = create_client(provider=provider_name, model=body.model, api_key=user_key)
        except LLMError as exc:
            raise ChatConfigurationError(str(exc)) from exc

        try:
            session = await self.runtime.prepare_session(
                client,
                provider_name,
                body.conversation_id,
                user_id=user.id,
            )
        except Exception:
            await close_client(client)
            raise
        return PreparedChat(client=client, provider_name=provider_name, session=session)

    def stream_events(
        self,
        prepared: PreparedChat,
        body: ChatRequest,
        *,
        tools,
    ) -> AsyncGenerator[RuntimeEvent, None]:
        """Return the runtime event source for a prepared chat turn.

        Wraps `ChatRuntime.run_session` so route code can drive the stream
        without knowing the service holds a runtime. The return type is an
        async generator — callers can both iterate and `aclose()` to force
        the runtime's `finally` block (releases the session lock, reconciles
        pending tool runs) rather than waiting on GC. Callers also own
        closing `prepared.client`.
        """
        return self.runtime.run_session(
            prepared.session,
            body.message,
            prepared.client,
            tools=tools,
            provider_name=prepared.provider_name,
            tool_choice=body.tool_choice,
        )

    async def run_message(
        self,
        body: ChatRequest,
        user: AuthenticatedUser,
        *,
        tools,
    ) -> ChatResponse:
        prepared = await self.prepare_chat(body, user)
        response_text = ""
        tool_calls_log: list[ToolCallLogEntry] = []
        hit_limit = False
        runtime_error = None

        try:
            async for event in self.stream_events(prepared, body, tools=tools):
                if isinstance(event, TextDeltaEvent) and event.text:
                    response_text += event.text
                elif isinstance(event, ToolPendingEvent):
                    tool_calls_log.append(
                        ToolCallLogEntry(
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
            return ChatResponse(
                conversation_id=prepared.session.id,
                response=response_text,
                tool_calls=[
                    ToolCallPreview(
                        tool=item.tool,
                        input=item.input,
                        result_preview=item.result_preview,
                    )
                    for item in tool_calls_log
                ],
                truncated=hit_limit,
            )
        finally:
            await close_client(prepared.client)
