"""Tiny in-memory sliding-window rate limiter for auth endpoints.

Keyed by client IP. Good enough for a self-hosted single-process app;
if this ever runs behind multiple workers or handles real traffic, swap
for slowapi or a Redis-backed limiter — the call surface (`.check(request)`
raising 429) is the same.

Not thread-locked — in FastAPI's single-event-loop asyncio model the
dict ops are atomic enough for attempt counting. If we ever move to a
multi-worker setup, revisit.
"""

from __future__ import annotations

import time
import asyncio
from collections import deque

from fastapi import HTTPException, Request, status


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Falls back to the socket address.

    If deployed behind a reverse proxy, the proxy should inject
    X-Forwarded-For and we should trust it — but trusting headers on a
    localhost default config is how you get spoofed limits, so we stick
    to request.client.host here. The proxy setup can swap this out.
    """
    if request.client is not None:
        return request.client.host or "unknown"
    return "unknown"


class RateLimiter:
    """Sliding-window counter: at most `max_attempts` in the last `window_seconds`."""

    def __init__(self, *, max_attempts: int, window_seconds: int):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def check(self, request: Request) -> None:
        """Record this attempt and raise 429 if the caller is over quota."""
        key = _client_ip(request)
        now = time.monotonic()
        cutoff = now - self.window_seconds
        bucket = self._hits.setdefault(key, deque())
        # Drop stale entries so the deque doesn't grow forever for a single IP.
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self.max_attempts:
            retry_after = max(1, int(bucket[0] + self.window_seconds - now))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts — try again later.",
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)

    def reset(self) -> None:
        """Clear all recorded hits — test helper."""
        self._hits.clear()


class ConcurrencyLimiter:
    """Process-local concurrency cap keyed by an arbitrary identifier.

    The backing state is intentionally isolated in one class so the chat router
    can swap to a shared-state implementation later without changing its flow.
    """

    def __init__(self, *, max_active: int):
        self.max_active = max_active
        self._active: dict[str | int, int] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: str | int, *, detail: str) -> None:
        async with self._lock:
            if self._active.get(key, 0) >= self.max_active:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=detail,
                )
            self._active[key] = self._active.get(key, 0) + 1

    async def release(self, key: str | int) -> None:
        async with self._lock:
            remaining = self._active.get(key, 0) - 1
            if remaining > 0:
                self._active[key] = remaining
            else:
                self._active.pop(key, None)

    def reset(self) -> None:
        self._active.clear()
