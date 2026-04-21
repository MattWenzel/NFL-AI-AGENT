"""Named in-memory coordination components for single-instance deployment."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Protocol
from fastapi import Request

from server.rate_limit import ConcurrencyLimiter, RateLimiter


class InMemoryPerUserLockRegistry:
    """Per-user lock registry behind an explicit interface."""

    def __init__(self):
        self._locks: dict[int, asyncio.Lock] = {}

    def for_user(self, user_id: int) -> asyncio.Lock:
        lock = self._locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[user_id] = lock
        return lock

    def reset(self) -> None:
        self._locks.clear()


@dataclass
class PendingCodexOAuthFlow:
    user_id: int
    device_auth_id: str
    user_code: str
    started_at: float
    task: asyncio.Task | None = None
    status: str = "pending"
    email: str | None = None
    error: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class InMemoryPendingCodexOAuthFlows:
    """Tracks in-flight device-code OAuth flows."""

    def __init__(self):
        self._flows: dict[str, PendingCodexOAuthFlow] = {}

    def create(
        self,
        pending_id: str,
        *,
        user_id: int,
        device_auth_id: str,
        user_code: str,
    ) -> PendingCodexOAuthFlow:
        flow = PendingCodexOAuthFlow(
            user_id=user_id,
            device_auth_id=device_auth_id,
            user_code=user_code,
            started_at=time.monotonic(),
        )
        self._flows[pending_id] = flow
        return flow

    def get(self, pending_id: str) -> PendingCodexOAuthFlow | None:
        return self._flows.get(pending_id)

    def pop(self, pending_id: str) -> PendingCodexOAuthFlow | None:
        return self._flows.pop(pending_id, None)

    def evict_terminal_older_than(self, max_age_seconds: float) -> None:
        now = time.monotonic()
        stale = [
            pending_id
            for pending_id, flow in self._flows.items()
            if flow.status != "pending" and now - flow.started_at > max_age_seconds
        ]
        for pending_id in stale:
            self._flows.pop(pending_id, None)

    def reset(self) -> None:
        self._flows.clear()

    async def cancel_all(self) -> None:
        for flow in list(self._flows.values()):
            task = flow.task
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._flows.clear()


class PerUserLockRegistry(Protocol):
    def for_user(self, user_id: int) -> asyncio.Lock: ...


class PendingCodexOAuthFlowStore(Protocol):
    def create(
        self,
        pending_id: str,
        *,
        user_id: int,
        device_auth_id: str,
        user_code: str,
    ) -> PendingCodexOAuthFlow: ...
    def get(self, pending_id: str) -> PendingCodexOAuthFlow | None: ...
    def pop(self, pending_id: str) -> PendingCodexOAuthFlow | None: ...
    def evict_terminal_older_than(self, max_age_seconds: float) -> None: ...
    async def cancel_all(self) -> None: ...


class AuthLimiterSet(Protocol):
    register_limiter: RateLimiter
    login_limiter: RateLimiter
    account_limiter: RateLimiter


class ChatStreamGate(Protocol):
    async def acquire(self, key: str | int, *, detail: str) -> None: ...
    async def release(self, key: str | int) -> None: ...


class RequestRateLimiter(Protocol):
    def check(self, request: Request) -> None: ...


@dataclass
class AppProcessState:
    """Process-local coordination state composed at app startup."""

    register_limiter: RateLimiter = field(
        default_factory=lambda: RateLimiter(max_attempts=5, window_seconds=15 * 60)
    )
    login_limiter: RateLimiter = field(
        default_factory=lambda: RateLimiter(max_attempts=10, window_seconds=15 * 60)
    )
    account_limiter: RateLimiter = field(
        default_factory=lambda: RateLimiter(max_attempts=20, window_seconds=15 * 60)
    )
    codex_start_limiter: RateLimiter = field(
        default_factory=lambda: RateLimiter(max_attempts=5, window_seconds=60 * 60)
    )
    chat_stream_limiter: ChatStreamGate = field(
        default_factory=lambda: ConcurrencyLimiter(max_active=3)
    )
    codex_pending_flows: PendingCodexOAuthFlowStore = field(
        default_factory=InMemoryPendingCodexOAuthFlows
    )
    codex_refresh_locks: PerUserLockRegistry = field(
        default_factory=InMemoryPerUserLockRegistry
    )

    async def aclose(self) -> None:
        await self.codex_pending_flows.cancel_all()


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
