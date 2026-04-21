"""FastAPI dependencies shared by HTTP routes."""

from fastapi import Request

from agent.runtime import ChatRuntime
from storage import RuntimeStore
from server.repositories import ConversationRepository, ExportRepository, RepositoryBundle, UserRepository


def get_store(request: Request) -> RuntimeStore:
    store = getattr(request.app.state, "runtime_store", None)
    if store is None:
        raise RuntimeError(
            "runtime_store not attached to app.state — the FastAPI lifespan must set it before requests run."
        )
    return store


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
