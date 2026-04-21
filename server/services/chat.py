"""Application service for chat request orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from auth.primitives import AuthenticatedUser
from agent.runtime import ChatRuntime
from provider import (
    BaseLLMClient,
    LLMError,
    create_client,
    get_default_provider,
    get_provider,
    provider_is_available,
)
from server.schemas.chat import ChatRequest, ChatResponse
from server.repositories import ConversationRepository, UserRepository
from auth import encryption
from auth.codex_credentials import (
    CodexCredentialError,
    resolve_access_token as _resolve_codex_access_token,
)
from server.process_state import InMemoryPerUserLockRegistry


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
    session: "SessionRecord"


async def resolve_user_credential(
    users: UserRepository,
    user_id: int,
    provider_name: str,
    *,
    refresh_locks: InMemoryPerUserLockRegistry,
) -> str | None:
    try:
        info = get_provider(provider_name)
    except KeyError:
        return None
    if info.credential_shape == "codex_oauth":
        try:
            return await _resolve_codex_access_token(
                users.store,
                user_id,
                provider_name,
                refresh_locks=refresh_locks,
            )
        except CodexCredentialError as exc:
            raise ChatConfigurationError(str(exc)) from exc
    rec = await users.get_api_key(user_id=user_id, provider=provider_name)
    if rec is None:
        return None
    try:
        return encryption.decrypt(rec.encrypted_key)
    except ValueError:
        return None


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
    except Exception:
        # Callers already have request-context logging.
        pass


class ChatApplicationService:
    """Owns chat request preparation and non-streaming response aggregation."""

    def __init__(
        self,
        runtime: ChatRuntime,
        users: UserRepository,
        conversations: ConversationRepository,
        *,
        refresh_locks: InMemoryPerUserLockRegistry,
    ):
        self.runtime = runtime
        self.users = users
        self.conversations = conversations
        self.refresh_locks = refresh_locks

    async def prepare_chat(
        self,
        body: ChatRequest,
        user: AuthenticatedUser,
    ) -> PreparedChat:
        if (
            body.conversation_id
            and await self.conversations.get_session(body.conversation_id, user_id=user.id) is None
        ):
            raise ChatNotFoundError("Conversation not found")

        provider_name = body.provider or get_default_provider()
        user_key = await resolve_user_credential(
            self.users,
            user.id,
            provider_name,
            refresh_locks=self.refresh_locks,
        )
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

    async def run_message(
        self,
        body: ChatRequest,
        user: AuthenticatedUser,
        *,
        tools,
    ) -> ChatResponse:
        prepared = await self.prepare_chat(body, user)
        response_text = ""
        tool_calls_log = []
        hit_limit = False
        runtime_error = None

        try:
            async for event in self.runtime.run_session(
                prepared.session,
                body.message,
                prepared.client,
                tools=tools,
                provider_name=prepared.provider_name,
                tool_choice=body.tool_choice,
            ):
                if event.type == "text_delta" and event.text:
                    response_text += event.text
                elif event.type == "tool_pending":
                    tool_calls_log.append({
                        "tool_run_id": event.tool_run_id,
                        "tool": event.name,
                        "input": event.input,
                        "result_preview": "",
                    })
                elif event.type in {"tool_completed", "tool_failed"} and tool_calls_log:
                    preview = (
                        event.result[:500]
                        if event.result and len(event.result) > 500
                        else event.result or event.error or ""
                    )
                    for item in reversed(tool_calls_log):
                        if item["tool_run_id"] == event.tool_run_id and not item["result_preview"]:
                            item["result_preview"] = preview
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
                    {
                        "tool": item["tool"],
                        "input": item["input"],
                        "result_preview": item["result_preview"],
                    }
                    for item in tool_calls_log
                ],
                truncated=hit_limit,
            )
        finally:
            await close_client(prepared.client)
