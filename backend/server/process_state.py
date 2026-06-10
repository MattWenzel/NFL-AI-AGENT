"""API-owned runtime state composed at FastAPI startup."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.config import (
    CHAT_REQUESTS_PER_QUARTER_HOUR,
    CHAT_STREAM_MAX_PER_USER,
    SQL_REQUESTS_PER_QUARTER_HOUR,
)
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
        default_factory=lambda: ConcurrencyLimiter(max_active=CHAT_STREAM_MAX_PER_USER)
    )
    # Per-user request volume on the LLM-spending endpoints (/chat/message,
    # /chat/stream, /database/helper-chat/stream). The concurrency limiter
    # above bounds parallelism; this bounds how many turns a single account
    # can start per window. Keyed by user id via check_key().
    chat_request_limiter: RateLimiter = field(
        default_factory=lambda: RateLimiter(
            max_attempts=CHAT_REQUESTS_PER_QUARTER_HOUR, window_seconds=15 * 60
        )
    )
    # Per-user ad-hoc SQL volume (/database/query, save-sql-as-report, the
    # Reports SQL editor). Each query can hold DuckDB CPU for up to its 30s
    # timeout, so volume is the cost lever. Keyed by user id via check_key().
    sql_query_limiter: RateLimiter = field(
        default_factory=lambda: RateLimiter(
            max_attempts=SQL_REQUESTS_PER_QUARTER_HOUR, window_seconds=15 * 60
        )
    )
    # Per-email cap on verification resends — the per-IP register_limiter
    # alone lets an IP-rotating bot burn the Resend quota. Keyed by the
    # normalized target email via check_key().
    email_resend_limiter: RateLimiter = field(
        default_factory=lambda: RateLimiter(max_attempts=3, window_seconds=60 * 60)
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
