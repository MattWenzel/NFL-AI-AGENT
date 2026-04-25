"""Unit tests for provider.retry."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from backend.providers.errors import RetryableError
from backend.providers.retry import (
    MAX_ATTEMPTS,
    compute_delay,
    parse_retry_after,
    parse_retry_after_ms,
    with_retries,
)


def test_parse_retry_after_seconds():
    assert parse_retry_after("3") == 3.0
    assert parse_retry_after("0.5") == 0.5
    assert parse_retry_after("0") == 0.0


def test_parse_retry_after_negative_clamped_to_zero():
    assert parse_retry_after("-5") == 0.0


def test_parse_retry_after_http_date():
    future = datetime.now(timezone.utc) + timedelta(seconds=10)
    delay = parse_retry_after(format_datetime(future))
    assert delay is not None and 5.0 < delay <= 11.0


def test_parse_retry_after_garbage_returns_none():
    assert parse_retry_after("not-a-date") is None
    assert parse_retry_after("") is None
    assert parse_retry_after(None) is None


def test_parse_retry_after_ms():
    assert parse_retry_after_ms("1500") == 1.5
    assert parse_retry_after_ms("0") == 0.0
    assert parse_retry_after_ms(None) is None
    assert parse_retry_after_ms("abc") is None


def test_compute_delay_uses_header_when_provided():
    # Header dominates; jitter doesn't apply.
    assert compute_delay(attempt=1, retry_after=7.0) == 7.0
    assert compute_delay(attempt=3, retry_after=2.5) == 2.5


def test_compute_delay_caps_header_at_120s():
    assert compute_delay(attempt=1, retry_after=999.0) == 120.0


def test_compute_delay_exponential_when_no_header():
    # Attempt 1 → ~2s, attempt 2 → ~4s, attempt 3 → ~8s, all ±25% jitter.
    for attempt, expected_base in [(1, 2.0), (2, 4.0), (3, 8.0)]:
        delay = compute_delay(attempt=attempt, retry_after=None)
        assert expected_base * 0.75 <= delay <= expected_base * 1.25, (
            f"attempt={attempt} delay={delay} expected≈{expected_base}"
        )


def test_compute_delay_caps_exponential_at_max():
    # Attempt 10 would compute 2 * 2^9 = 1024s; must cap at MAX_DELAY=30.
    delay = compute_delay(attempt=10, retry_after=None)
    assert 30.0 * 0.75 <= delay <= 30.0 * 1.25


@pytest.mark.asyncio
async def test_with_retries_returns_on_first_success():
    calls = 0

    async def op():
        nonlocal calls
        calls += 1
        return "ok"

    result = await with_retries(op, classify=lambda e: None)
    assert result == "ok"
    assert calls == 1


@pytest.mark.asyncio
async def test_with_retries_propagates_non_retryable():
    calls = 0

    async def op():
        nonlocal calls
        calls += 1
        raise ValueError("nope")

    with pytest.raises(ValueError):
        await with_retries(op, classify=lambda e: None)
    assert calls == 1  # no retry


@pytest.mark.asyncio
async def test_with_retries_retries_classified_then_succeeds(monkeypatch):
    # Stub asyncio.sleep so the test runs instantly.
    sleep_calls: list[float] = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("backend.providers.retry.asyncio.sleep", fake_sleep)

    calls = 0

    async def op():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError(f"transient {calls}")
        return "recovered"

    def classify(exc):
        return RetryableError(exc, retry_after_seconds=0.01)

    result = await with_retries(op, classify=classify)
    assert result == "recovered"
    assert calls == 3
    assert sleep_calls == [0.01, 0.01]  # two sleeps before the third success


@pytest.mark.asyncio
async def test_with_retries_exhausts_and_raises_last_exception(monkeypatch):
    async def fake_sleep(seconds):
        return None
    monkeypatch.setattr("backend.providers.retry.asyncio.sleep", fake_sleep)

    calls = 0

    async def op():
        nonlocal calls
        calls += 1
        raise RuntimeError(f"attempt {calls}")

    with pytest.raises(RuntimeError, match=f"attempt {MAX_ATTEMPTS}"):
        await with_retries(op, classify=lambda e: RetryableError(e))
    assert calls == MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_with_retries_on_retry_callback_fires(monkeypatch):
    async def fake_sleep(seconds):
        return None
    monkeypatch.setattr("backend.providers.retry.asyncio.sleep", fake_sleep)

    callbacks: list[tuple[int, float, str]] = []

    async def on_retry(attempt, delay, exc):
        callbacks.append((attempt, delay, str(exc)))

    calls = 0

    async def op():
        nonlocal calls
        calls += 1
        if calls < 2:
            raise RuntimeError("once")
        return "done"

    await with_retries(
        op,
        classify=lambda e: RetryableError(e, retry_after_seconds=0.0),
        on_retry=on_retry,
    )
    assert len(callbacks) == 1
    attempt, delay, msg = callbacks[0]
    assert attempt == 1
    assert msg == "once"


@pytest.mark.asyncio
async def test_with_retries_callback_failure_does_not_abort(monkeypatch):
    async def fake_sleep(seconds):
        return None
    monkeypatch.setattr("backend.providers.retry.asyncio.sleep", fake_sleep)

    async def bad_callback(attempt, delay, exc):
        raise RuntimeError("callback exploded")

    calls = 0

    async def op():
        nonlocal calls
        calls += 1
        if calls < 2:
            raise RuntimeError("transient")
        return "done"

    result = await with_retries(
        op,
        classify=lambda e: RetryableError(e, retry_after_seconds=0.0),
        on_retry=bad_callback,
    )
    assert result == "done"
