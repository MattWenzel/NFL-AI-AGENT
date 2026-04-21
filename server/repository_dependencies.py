"""Request-scoped dependency factories for the runtime store + slim
conversation mapping repository.

Kept separate from `server/repositories.py` (framework-agnostic
adapter) and `server/dependencies.py` (service-layer factories that
would otherwise pull `auth.primitives` into a cycle).

Any module that needs a FastAPI `Depends(...)` handle on the persistence
layer imports from here. That includes `auth/primitives.py` — routing
through this module instead of `server.dependencies` breaks the
`auth.primitives` ↔ `server.dependencies` cycle without any
`TYPE_CHECKING` or lazy-import workarounds.
"""

from __future__ import annotations

from fastapi import Request

from server.repositories import ConversationRepository
from storage import RuntimeStore


def get_store(request: Request) -> RuntimeStore:
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise RuntimeError(
            "store not attached to app.state — the FastAPI lifespan must set it before requests run."
        )
    return store


def get_conversation_repository(request: Request) -> ConversationRepository:
    """Request-scoped factory for the slim `ConversationRepository`.

    Wraps the app-state `RuntimeStore`; the repo itself holds no state
    beyond the store reference, so constructing one per request is fine.
    """
    return ConversationRepository(get_store(request))
