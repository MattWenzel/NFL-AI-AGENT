"""FastAPI dependencies for auth, persistence, and service factories."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status

from agent.runtime import ChatRuntime
from auth.primitives import AuthenticatedUser, _extract_session_token
from config import AUTH_SESSION_TOUCH_INTERVAL_SECONDS, EXPORTS_DIR
from server.process_state import (
    AppProcessState,
    ChatStreamGate,
    PendingCodexOAuthFlowStore,
    PerUserLockRegistry,
    RequestRateLimiter,
)
from server.services.auth import AuthService
from server.services.chat import ChatService
from server.services.codex_oauth import CodexOAuthService
from server.services.conversations import ConversationService
from server.services.exports import ExportService
from server.services.google_oauth import GoogleOAuthService
from server.services.settings import SettingsService
from storage import RuntimeStore


def get_store(request: Request) -> RuntimeStore:
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise RuntimeError(
            "store not attached to app.state — the FastAPI lifespan must set it before requests run."
        )
    return store


async def _resolve_user(request: Request, store: RuntimeStore) -> AuthenticatedUser | None:
    token = _extract_session_token(request)
    if not token:
        return None
    session = await store.get_auth_session(token)
    if session is None:
        return None
    if session.expires_at < datetime.now(timezone.utc).isoformat():
        await store.delete_auth_session(token)
        return None
    user = await store.get_user_by_id(session.user_id)
    if user is None:
        await store.delete_auth_session(token)
        return None
    await store.touch_auth_session(
        token,
        min_interval_seconds=AUTH_SESSION_TOUCH_INTERVAL_SECONDS,
        last_used_at=session.last_used_at,
    )
    return AuthenticatedUser.from_record(user)


async def get_current_user(
    request: Request,
    store: RuntimeStore = Depends(get_store),
) -> AuthenticatedUser:
    user = await _resolve_user(request, store)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_user_optional(
    request: Request,
    store: RuntimeStore = Depends(get_store),
) -> AuthenticatedUser | None:
    return await _resolve_user(request, store)


def get_runtime(request: Request) -> ChatRuntime:
    runtime = getattr(request.app.state, "chat_runtime", None)
    if runtime is None:
        raise RuntimeError(
            "chat_runtime not attached to app.state — the FastAPI lifespan must set it before requests run."
        )
    return runtime


def get_process_state(request: Request) -> AppProcessState:
    state = getattr(request.app.state, "process_state", None)
    if state is None:
        raise RuntimeError(
            "process_state not attached to app.state — the FastAPI lifespan must set it before requests run."
        )
    return state


def get_chat_stream_gate(request: Request) -> ChatStreamGate:
    return get_process_state(request).chat_stream_limiter


def get_codex_start_limiter(request: Request) -> RequestRateLimiter:
    return get_process_state(request).codex_start_limiter


def get_codex_pending_flows(request: Request) -> PendingCodexOAuthFlowStore:
    return get_process_state(request).codex_pending_flows


def get_codex_refresh_locks(request: Request) -> PerUserLockRegistry:
    return get_process_state(request).codex_refresh_locks


def get_chat_service(
    runtime: ChatRuntime = Depends(get_runtime),
    store: RuntimeStore = Depends(get_store),
    refresh_locks: PerUserLockRegistry = Depends(get_codex_refresh_locks),
) -> ChatService:
    return ChatService(
        runtime,
        store,
        refresh_locks=refresh_locks,
    )


def get_conversation_service(
    store: RuntimeStore = Depends(get_store),
) -> ConversationService:
    return ConversationService(store)


def get_codex_oauth_service(
    store: RuntimeStore = Depends(get_store),
    pending_flows: PendingCodexOAuthFlowStore = Depends(get_codex_pending_flows),
) -> CodexOAuthService:
    return CodexOAuthService(store, pending_flows)


def get_export_service(
    store: RuntimeStore = Depends(get_store),
) -> ExportService:
    return ExportService(store)


def get_auth_service(
    store: RuntimeStore = Depends(get_store),
) -> AuthService:
    return AuthService(store, EXPORTS_DIR)


def get_settings_service(
    store: RuntimeStore = Depends(get_store),
) -> SettingsService:
    return SettingsService(store)


def get_google_oauth_service(
    store: RuntimeStore = Depends(get_store),
    process_state: AppProcessState = Depends(get_process_state),
) -> GoogleOAuthService:
    return GoogleOAuthService(store, process_state.google_oauth_flows)
