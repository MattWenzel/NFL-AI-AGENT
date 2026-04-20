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
