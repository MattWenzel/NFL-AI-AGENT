"""Unit tests for provider.overflow.is_context_overflow."""

import pytest

from backend.core.providers.overflow import is_context_overflow


@pytest.mark.parametrize(
    "message",
    [
        "Error: prompt is too long: 250000 tokens",
        "PROMPT IS TOO LONG",
        "input is too long for this model",
        "This request's context window has been exceeded",
        "exceeds the context window of 200000",
        "Maximum context length is 128000 tokens",
        "maximum prompt length is 200000",
        "context_length_exceeded",
        "input token count of 250000 exceeds the maximum of 200000",
        "too many tokens in the request",
        "Too many input tokens",
    ],
)
def test_matches_context_overflow_phrases(message):
    assert is_context_overflow(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "",
        "Invalid API key",
        "Rate limit exceeded",
        "Internal server error",
        "bad request",
        "The model `claude-foo` does not exist",
    ],
)
def test_ignores_non_overflow_errors(message):
    assert is_context_overflow(message) is False


def test_handles_none_gracefully():
    assert is_context_overflow(None) is False  # type: ignore[arg-type]
