"""Chat process: schemas, errors, DTOs, and application service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncGenerator, Literal

from pydantic import BaseModel, Field

from backend.agent.events import (
    RuntimeErrorEvent,
    RuntimeEvent,
    TextDeltaEvent,
    ToolCompletedEvent,
    ToolFailedEvent,
    ToolPendingEvent,
)
from backend.agent.runtime import ChatRuntime
from backend.persistence import RuntimeStore, SessionRecord
from backend.processes.oauth.credentials import ProviderCredentialService
from backend.processes.oauth.errors import CredentialServiceError
from backend.providers import (
    create_client,
    get_default_provider,
    get_provider,
    provider_is_available,
)
from backend.providers.base import BaseLLMClient
from backend.providers.errors import LLMError
from backend.runtime_state import PerUserLockRegistry
from backend.security.types import AuthenticatedUser


class ChatServiceError(Exception):
    """Base class for application-service chat failures."""


class ChatNotFoundError(ChatServiceError):
    """The referenced conversation does not exist for the caller."""


class ChatConfigurationError(ChatServiceError):
    """Provider, credential, or model selection failed."""


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000, description="User message")
    conversation_id: str | None = Field(None, description="Existing conversation ID (omit to create new)")
    provider: str | None = Field(None, description="LLM provider (anthropic, openai)")
    model: str | None = Field(None, description="Model name override")
    tool_choice: Literal["auto", "required", "none"] | None = Field(
        None,
        description=(
            "Tool-use control for this turn: 'auto' (default — model chooses), "
            "'required' (force a tool call), 'none' (text only). Omit or null "
            "to use the model's default behavior."
        ),
    )


class ToolCallPreview(BaseModel):
    tool: str
    input: dict
    result_preview: str


class ChatResponse(BaseModel):
    conversation_id: str
    response: str
    tool_calls: list[ToolCallPreview] = Field(default_factory=list)
    truncated: bool = Field(False, description="True when the agent hit its iteration limit")


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
        """Return the runtime event source for a prepared chat turn."""
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
