"""FastAPI dependencies for service-layer factories.

Store + repository getters live in `server.repository_dependencies` so
`auth.primitives` can import them without pulling the service layer (and
therefore itself) into a circular import.
"""

from __future__ import annotations

from fastapi import Depends, Request

from agent.runtime import ChatRuntime
from server.process_state import (
    PendingCodexOAuthFlowStore,
    PerUserLockRegistry,
    get_codex_pending_flows,
    get_codex_refresh_locks,
)
from server.repositories import ConversationRepository
from server.repository_dependencies import (
    get_conversation_repository,
    get_store,
)
from server.services.chat import ChatApplicationService
from server.services.codex_oauth import CodexOAuthApplicationService
from server.services.conversations import ConversationApplicationService
from storage import RuntimeStore


def get_runtime(request: Request) -> ChatRuntime:
    runtime = getattr(request.app.state, "chat_runtime", None)
    if runtime is None:
        raise RuntimeError(
            "chat_runtime not attached to app.state — the FastAPI lifespan must set it before requests run."
        )
    return runtime


def get_chat_service(
    runtime: ChatRuntime = Depends(get_runtime),
    store: RuntimeStore = Depends(get_store),
    refresh_locks: PerUserLockRegistry = Depends(get_codex_refresh_locks),
) -> ChatApplicationService:
    return ChatApplicationService(
        runtime,
        store,
        refresh_locks=refresh_locks,
    )


def get_conversation_service(
    store: RuntimeStore = Depends(get_store),
    conversations: ConversationRepository = Depends(get_conversation_repository),
) -> ConversationApplicationService:
    return ConversationApplicationService(store, conversations)


def get_codex_oauth_service(
    store: RuntimeStore = Depends(get_store),
    pending_flows: PendingCodexOAuthFlowStore = Depends(get_codex_pending_flows),
) -> CodexOAuthApplicationService:
    return CodexOAuthApplicationService(store, pending_flows)
