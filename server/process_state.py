"""Named in-memory coordination components for single-instance deployment."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Protocol

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


@dataclass
class PendingGoogleOAuthFlow:
    """State carried across Google's consent-screen redirect.

    `code_verifier` and `nonce` must travel from `/auth/oauth/google/start`
    to `/auth/oauth/google/callback` without being visible to the browser.
    Keeping them in memory avoids a cookie round-trip; the keying `state`
    parameter is what the browser echoes back, so the server can look up
    the rest. `user_id` is set for link flows (started from settings)
    so the callback knows to attach the identity rather than sign in.
    """

    code_verifier: str
    nonce: str
    created_at: float
    user_id: int | None = None  # None → sign-in/sign-up flow; int → link flow


class InMemoryPendingGoogleOAuthFlows:
    """Process-local pending-flow registry for Google OAuth sign-ins.

    Keyed by the OAuth `state` parameter. 10-minute TTL — if the user takes
    longer than that at Google's consent screen, they restart. Evictions
    run lazily on each `create` so stale entries don't accumulate.
    """

    _STALE_AFTER_SECONDS = 10 * 60

    def __init__(self):
        self._flows: dict[str, PendingGoogleOAuthFlow] = {}

    def create(
        self,
        state: str,
        *,
        code_verifier: str,
        nonce: str,
        user_id: int | None = None,
    ) -> PendingGoogleOAuthFlow:
        self._evict_stale()
        flow = PendingGoogleOAuthFlow(
            code_verifier=code_verifier,
            nonce=nonce,
            created_at=time.monotonic(),
            user_id=user_id,
        )
        self._flows[state] = flow
        return flow

    def pop(self, state: str) -> PendingGoogleOAuthFlow | None:
        return self._flows.pop(state, None)

    def _evict_stale(self) -> None:
        now = time.monotonic()
        stale = [
            key for key, flow in self._flows.items()
            if now - flow.created_at > self._STALE_AFTER_SECONDS
        ]
        for key in stale:
            self._flows.pop(key, None)

    def reset(self) -> None:
        self._flows.clear()


class PendingGoogleOAuthFlowStore(Protocol):
    def create(
        self,
        state: str,
        *,
        code_verifier: str,
        nonce: str,
        user_id: int | None = None,
    ) -> PendingGoogleOAuthFlow: ...
    def pop(self, state: str) -> PendingGoogleOAuthFlow | None: ...
    def reset(self) -> None: ...


class ChatStreamGate(Protocol):
    async def acquire(self, key: str | int, *, detail: str) -> None: ...
    async def release(self, key: str | int) -> None: ...


class RequestRateLimiter(Protocol):
    def check(self, request: object) -> None: ...


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
    google_oauth_flows: PendingGoogleOAuthFlowStore = field(
        default_factory=InMemoryPendingGoogleOAuthFlows
    )

    async def aclose(self) -> None:
        await self.codex_pending_flows.cancel_all()
