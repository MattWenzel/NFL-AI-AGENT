"""Request-scoped repository dependency factories for FastAPI.

Kept separate from `server/repositories.py` (pure adapters over
`RuntimeStore` — no web-framework wiring) and from `server/dependencies.py`
(which holds service-layer factories that would otherwise pull
`auth.primitives` into a cycle).

Any module needing a FastAPI `Depends(...)` handle on a repository
imports from here. That includes `auth/primitives.py` — routing the
import here instead of through `server.dependencies` breaks the
`auth.primitives` ↔ `server.dependencies` cycle cleanly, with no
`TYPE_CHECKING` or lazy-import workarounds.
"""

from __future__ import annotations

from fastapi import Request

from server.repositories import (
    ConversationRepository,
    ExportRepository,
    RepositoryBundle,
    UserRepository,
)


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
