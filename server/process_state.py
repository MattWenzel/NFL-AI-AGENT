"""Named in-memory coordination components for single-instance deployment."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


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
