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

from backend.server.request_context import client_ip_key


class RateLimiter:
    """Sliding-window counter: at most `max_attempts` in the last `window_seconds`."""

    def __init__(self, *, max_attempts: int, window_seconds: int):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._last_global_prune = 0.0

    def _prune_empty_buckets(self, *, now: float, cutoff: float) -> None:
        # Distinct client IPs can otherwise grow `_hits` unbounded over time.
        # Sweep infrequently so hot-path checks stay cheap.
        if now - self._last_global_prune < self.window_seconds:
            return
        empty_keys = [key for key, bucket in self._hits.items() if not bucket or bucket[-1] < cutoff]
        for key in empty_keys:
            self._hits.pop(key, None)
        self._last_global_prune = now

    def check(self, request: Request) -> None:
        """Record this attempt and raise 429 if the caller's IP is over quota."""
        self.check_key(client_ip_key(request))

    def check_key(self, key: str) -> None:
        """Record this attempt and raise 429 if `key` (an IP, a user id, an
        email — whatever identity the endpoint meters) is over quota."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        self._prune_empty_buckets(now=now, cutoff=cutoff)
        bucket = self._hits.setdefault(key, deque())
        # Drop stale entries so the deque doesn't grow forever for a single IP.
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if not bucket and key in self._hits:
            self._hits.pop(key, None)
            bucket = self._hits.setdefault(key, deque())
        if len(bucket) >= self.max_attempts:
            retry_after = max(1, int(bucket[0] + self.window_seconds - now))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts — try again later.",
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)


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
