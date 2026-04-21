"""Application service for chat request orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import AsyncGenerator

from auth.primitives import AuthenticatedUser
from agent.events import RuntimeEvent
from agent.runtime import ChatRuntime
from provider import (
    BaseLLMClient,
    LLMError,
    create_client,
    get_default_provider,
    get_provider,
    provider_is_available,
)
from storage import RuntimeStore, SessionRecord
from server.schemas.chat import ChatRequest, ChatResponse
from server.process_state import PerUserLockRegistry
from server.services.credentials import CredentialServiceError, ProviderCredentialService


class ChatServiceError(Exception):
    """Base class for application-service chat failures."""


class ChatNotFoundError(ChatServiceError):
    """The referenced conversation does not exist for the caller."""


class ChatConfigurationError(ChatServiceError):
    """Provider/credential/model selection failed in an application-specific way."""


@dataclass
class PreparedChat:
    client: BaseLLMClient
    provider_name: str
    session: SessionRecord


@dataclass
class ToolCallLogEntry:
    tool_run_id: str
    tool: str
    input: dict
    result_preview: str = ""


def create_client_for_request(
    provider: str | None = None,
    model: str | None = None,
    *,
    api_key: str | None = None,
) -> BaseLLMClient:
    provider_name = provider or get_default_provider()
    try:
        info = get_provider(provider_name)
    except KeyError as exc:
        raise ChatConfigurationError(str(exc)) from exc

    if not api_key and not provider_is_available(info):
        if info.credential_shape == "codex_oauth":
            raise ChatConfigurationError(
                f"{info.display_name} not connected — click Connect ChatGPT in Settings."
            )
        raise ChatConfigurationError(
            f"No API key for {info.display_name} — add one in Settings."
        )

    try:
        return create_client(provider=provider_name, model=model, api_key=api_key)
    except LLMError as exc:
        raise ChatConfigurationError(str(exc)) from exc


async def close_client(client: BaseLLMClient) -> None:
    try:
        await client.aclose()
    except (LLMError, OSError, RuntimeError):
        # Callers already have request-context logging.
        pass


class ChatApplicationService:
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
            and await self.store.get_session_async(body.conversation_id, user_id=user.id) is None
        ):
            raise ChatNotFoundError("Conversation not found")

        provider_name = body.provider or get_default_provider()
        try:
            user_key = await self.credentials.get_api_key(
                user_id=user.id,
                provider_name=provider_name,
            )
        except CredentialServiceError as exc:
            raise ChatConfigurationError(str(exc)) from exc
        client = create_client_for_request(body.provider, body.model, api_key=user_key)
        try:
            session = await self.runtime.prepare_session_async(
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
                if event.type == "text_delta" and event.text:
                    response_text += event.text
                elif event.type == "tool_pending":
                    tool_calls_log.append(
                        ToolCallLogEntry(
                            tool_run_id=event.tool_run_id,
                            tool=event.name,
                            input=event.input,
                        )
                    )
                elif event.type in {"tool_completed", "tool_failed"} and tool_calls_log:
                    preview = (
                        event.result[:500]
                        if event.result and len(event.result) > 500
                        else event.result or event.error or ""
                    )
                    for item in reversed(tool_calls_log):
                        if item.tool_run_id == event.tool_run_id and not item.result_preview:
                            item.result_preview = preview
                            break
                elif event.type == "runtime_error":
                    runtime_error = event.error or "Runtime error"
                    hit_limit = bool(runtime_error.startswith("Reached maximum tool iterations"))
            if runtime_error and not hit_limit:
                raise ChatServiceError(runtime_error)
            return ChatResponse(
                conversation_id=prepared.session.id,
                response=response_text,
                tool_calls=[
                    asdict(item, dict_factory=lambda items: {k: v for k, v in items if k != "tool_run_id"})
                    for item in tool_calls_log
                ],
                truncated=hit_limit,
            )
        finally:
            await close_client(prepared.client)
