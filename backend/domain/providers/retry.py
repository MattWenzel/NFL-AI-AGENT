"""Header-aware exponential backoff for transient LLM errors.

Wraps a single coroutine that calls a provider; on retryable failure,
sleeps according to the provider's Retry-After header (when present)
or an exponential schedule, then retries up to MAX_ATTEMPTS times.
Non-retryable errors propagate immediately.

Stream wrapping is the caller's responsibility — once a stream has
yielded any event, retries become unsafe (we'd double-emit). Providers
retry only the stream-open phase before any event has flown.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TypeVar

from backend.domain.providers.errors import RetryableError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Initial attempt + 3 retries. Most transient overloads recover within
# 2-3 retries; beyond that the user is better off seeing the error and
# rephrasing.
MAX_ATTEMPTS = 4
BASE_DELAY = 2.0
MAX_DELAY = 30.0          # cap on computed backoff when no header given
HEADER_MAX_DELAY = 120.0  # cap on header-instructed waits (sane upper bound)
JITTER_FRAC = 0.25        # ±25% jitter on computed delay


def parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header (delta-seconds or HTTP-date)."""
    if not value:
        return None
    raw = value.strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


def parse_retry_after_ms(value: str | None) -> float | None:
    """Parse an `retry-after-ms` header (integer milliseconds)."""
    if value is None:
        return None
    try:
        return max(0.0, float(value) / 1000.0)
    except (TypeError, ValueError):
        return None


def compute_delay(attempt: int, retry_after: float | None) -> float:
    """Pick the sleep duration for the next retry attempt.

    Header-instructed delay wins when present (capped at HEADER_MAX_DELAY).
    Otherwise exponential backoff with ±25% jitter, capped at MAX_DELAY.
    """
    if retry_after is not None and retry_after > 0:
        return min(retry_after, HEADER_MAX_DELAY)
    base = BASE_DELAY * (2 ** (attempt - 1))
    delay = min(base, MAX_DELAY)
    jitter = delay * JITTER_FRAC
    return max(0.0, delay + random.uniform(-jitter, jitter))


async def with_retries(
    op: Callable[[], Awaitable[T]],
    *,
    classify: Callable[[Exception], RetryableError | None],
    on_retry: Callable[[int, float, Exception], Awaitable[None]] | None = None,
) -> T:
    """Run `op`, retrying on classifier-approved exceptions.

    `classify(exc)` returns a `RetryableError` (with optional
    retry_after_seconds) when the exception is safe to retry, or None
    when the exception should propagate immediately.

    `on_retry(attempt, delay_seconds, original_exc)` runs *before* the
    sleep on each retry. Use it to surface a user-visible signal
    ("retrying after rate limit"). Failures inside the callback are
    logged but don't abort the retry.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await op()
        except Exception as exc:
            classification = classify(exc)
            if classification is None or attempt >= MAX_ATTEMPTS:
                raise
            delay = compute_delay(attempt, classification.retry_after_seconds)
            logger.info(
                "retrying after %s (attempt %d/%d, sleep %.1fs): %s",
                type(exc).__name__, attempt, MAX_ATTEMPTS, delay, exc,
            )
            if on_retry is not None:
                try:
                    await on_retry(attempt, delay, exc)
                except Exception:
                    logger.exception("on_retry callback raised; continuing with sleep")
            await asyncio.sleep(delay)
    # Loop exits via either return or raise; this is unreachable.
    raise RuntimeError("with_retries: exhausted attempts without raising")
