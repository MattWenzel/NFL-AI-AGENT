"""API-owned runtime state composed at FastAPI startup."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.server.rate_limit import ConcurrencyLimiter, RateLimiter
from backend.runtime_state import (
    PendingCodexOAuthFlows,
    PendingGoogleOAuthFlows,
    PerUserLockRegistry,
)


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
    chat_stream_limiter: ConcurrencyLimiter = field(
        default_factory=lambda: ConcurrencyLimiter(max_active=3)
    )
    codex_pending_flows: PendingCodexOAuthFlows = field(
        default_factory=PendingCodexOAuthFlows
    )
    codex_refresh_locks: PerUserLockRegistry = field(
        default_factory=PerUserLockRegistry
    )
    google_oauth_flows: PendingGoogleOAuthFlows = field(
        default_factory=PendingGoogleOAuthFlows
    )

    async def aclose(self) -> None:
        await self.codex_pending_flows.cancel_all()
