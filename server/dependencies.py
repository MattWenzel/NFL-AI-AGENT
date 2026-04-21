"""FastAPI dependencies shared by HTTP routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, Request

from agent.runtime import ChatRuntime
from server.process_state import (
    PendingCodexOAuthFlowStore,
    PerUserLockRegistry,
    get_codex_pending_flows,
    get_codex_refresh_locks,
)
from server.repositories import ConversationRepository, ExportRepository, RepositoryBundle, UserRepository

# Service classes are imported lazily at runtime inside the factory
# functions below to avoid a circular import: `auth.primitives` imports
# from this module, and the service layer imports `auth.primitives`.
# Under TYPE_CHECKING the imports are re-exposed purely as type hints,
# which lets editors and static checkers see the return shapes without
# triggering the cycle at import time.
if TYPE_CHECKING:
    from server.services.chat import ChatApplicationService
    from server.services.codex_oauth import CodexOAuthApplicationService
    from server.services.conversations import ConversationApplicationService


def get_runtime(request: Request) -> ChatRuntime:
    runtime = getattr(request.app.state, "chat_runtime", None)
    if runtime is None:
        raise RuntimeError(
            "chat_runtime not attached to app.state — the FastAPI lifespan must set it before requests run."
        )
    return runtime


def get_repositories(request: Request) -> RepositoryBundle:
    repositories = getattr(request.app.state, "repositories", None)
    if repositories is None:
        raise RuntimeError(
            "repositories not attached to app.state — the FastAPI lifespan must set it before requests run."
        )
    return repositories


def get_user_repository(request: Request) -> UserRepository:
    return get_repositories(request).users


def get_conversation_repository(request: Request) -> ConversationRepository:
    return get_repositories(request).conversations


def get_export_repository(request: Request) -> ExportRepository:
    return get_repositories(request).exports


def get_chat_service(
    runtime: ChatRuntime = Depends(get_runtime),
    users: UserRepository = Depends(get_user_repository),
    conversations: ConversationRepository = Depends(get_conversation_repository),
    refresh_locks: PerUserLockRegistry = Depends(get_codex_refresh_locks),
) -> ChatApplicationService:
    from server.services.chat import ChatApplicationService
    return ChatApplicationService(
        runtime,
        users,
        conversations,
        refresh_locks=refresh_locks,
    )


def get_conversation_service(
    conversations: ConversationRepository = Depends(get_conversation_repository),
) -> ConversationApplicationService:
    from server.services.conversations import ConversationApplicationService
    return ConversationApplicationService(conversations)


def get_codex_oauth_service(
    users: UserRepository = Depends(get_user_repository),
    pending_flows: PendingCodexOAuthFlowStore = Depends(get_codex_pending_flows),
) -> CodexOAuthApplicationService:
    from server.services.codex_oauth import CodexOAuthApplicationService
    return CodexOAuthApplicationService(users, pending_flows)
