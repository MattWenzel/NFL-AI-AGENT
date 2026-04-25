"""Anthropic Claude provider implementation."""

import asyncio
import json
import logging
import os
import sys
import time
from typing import AsyncIterator

import anthropic

from backend.lib.providers.base import BaseLLMClient
from backend.lib.providers.errors import ContextOverflowError, LLMError, RetryableError
from backend.lib.providers.overflow import is_context_overflow
from backend.lib.providers.retry import (
    MAX_ATTEMPTS, compute_delay, parse_retry_after,
    parse_retry_after_ms, with_retries,
)
from backend.lib.providers.types import (
    ANTHROPIC, Message, MessageResponse, RetryingEvent, StopReason, TextEvent,
    ToolChoice, ToolDefinition, ToolUseEvent, Usage,
)
from backend.lib.providers.tool_calls import build_tool_use_event

logger = logging.getLogger(__name__)

_STOP_MAP: dict[str, StopReason] = {
    "end_turn": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
}

# Canonical ToolChoice → Anthropic wire format. Anthropic uses a dict with
# "any" (not "required") for the "must call some tool" case; the other two
# map onto `auto` / `none` directly.
_ANTHROPIC_TOOL_CHOICE: dict[str, dict] = {
    "auto":     {"type": "auto"},
    "required": {"type": "any"},
    "none":     {"type": "none"},
}


def _retry_after_from_response(exc: Exception) -> float | None:
    """Extract a delay (seconds) from Anthropic's response headers."""
    response = getattr(exc, "response", None)
    if response is None:
        return None
    headers = getattr(response, "headers", None) or {}
    # `retry-after-ms` is more precise when present; fall back to standard.
    return (
        parse_retry_after_ms(headers.get("retry-after-ms"))
        or parse_retry_after(headers.get("retry-after"))
    )


def classify_anthropic_error(exc: Exception) -> RetryableError | None:
    """Decide whether an Anthropic SDK exception is safe to retry.

    Retry on rate limits, 5xx (incl. 529 overloaded), connection
    errors, and request timeouts. Auth, bad-request, and other 4xx
    are not retryable — they'll fail again on retry.
    """
    if isinstance(exc, anthropic.RateLimitError):
        return RetryableError(exc, retry_after_seconds=_retry_after_from_response(exc))
    if isinstance(exc, anthropic.APIStatusError):
        status = getattr(exc, "status_code", None)
        if status is not None and (status == 529 or 500 <= status < 600):
            return RetryableError(exc, retry_after_seconds=_retry_after_from_response(exc))
        return None
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return RetryableError(exc)
    return None


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude client with streaming support."""

    def __init__(self, model: str, *, max_output_tokens: int = 16384, api_key: str | None = None):
        super().__init__(model, max_output_tokens=max_output_tokens)
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
        )

    @property
    def provider_name(self) -> str:
        return ANTHROPIC

    def _translate_error(self, exc: Exception) -> LLMError:
        if isinstance(exc, anthropic.AuthenticationError):
            return LLMError("Invalid Anthropic API key — update it in Settings")
        if isinstance(exc, anthropic.RateLimitError):
            return LLMError("Rate limited by Anthropic — retry shortly")
        if isinstance(exc, anthropic.BadRequestError):
            # 400 with a "prompt is too long" body → trigger compaction.
            body = self._error_body(exc)
            if is_context_overflow(body):
                return ContextOverflowError(
                    "Prompt exceeded Anthropic context window — compacting and retrying"
                )
        if isinstance(exc, anthropic.APIError):
            return LLMError(f"Anthropic API error: {exc.message}")
        return LLMError(f"Anthropic error: {exc}")

    @staticmethod
    def _error_body(exc: Exception) -> str:
        """Best-effort extract of the error message + response body for matching."""
        parts = [str(exc)]
        message = getattr(exc, "message", None)
        if message:
            parts.append(str(message))
        response = getattr(exc, "response", None)
        if response is not None:
            text = getattr(response, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return " ".join(parts)

    async def create_message(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        model: str | None = None,
    ) -> MessageResponse:
        kwargs = self._build_kwargs(
            self._convert_messages(messages), tools, system,
        )
        if model:
            kwargs["model"] = model
        t0 = time.monotonic()
        async with self._wrap_api_errors():
            response = await with_retries(
                lambda: self._client.messages.create(**kwargs),
                classify=classify_anthropic_error,
            )
        duration = time.monotonic() - t0
        parsed = self._parse_response(response)
        self._set_last_usage(parsed.usage)
        self._set_last_stop_reason(parsed.stop_reason)
        cache_read, cache_write = self._extract_cache_usage(getattr(response, "usage", None))
        logger.debug(
            "LLM create  model=%s  in=%d out=%d  cache_read=%d cache_write=%d  stop=%s  %.1fs",
            kwargs["model"], parsed.usage.input_tokens, parsed.usage.output_tokens,
            cache_read, cache_write, parsed.stop_reason, duration,
        )
        return parsed

    async def stream_message(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[TextEvent | ToolUseEvent | RetryingEvent]:
        kwargs = self._build_kwargs(
            self._convert_messages(messages), tools, system, tool_choice,
        )
        t0 = time.monotonic()
        # Retry the stream-OPEN phase only. Once the stream is established
        # and we've started yielding events, retrying would double-emit
        # text — fail the turn instead. The Anthropic SDK opens the HTTP
        # connection inside `__aenter__`, so transient overload errors
        # surface there.
        stream_ctx = None
        stream = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                stream_ctx = self._client.messages.stream(**kwargs)
                stream = await stream_ctx.__aenter__()
                break
            except Exception as exc:
                classification = classify_anthropic_error(exc)
                if classification is None or attempt >= MAX_ATTEMPTS:
                    raise self._translate_error(exc)
                delay = compute_delay(attempt, classification.retry_after_seconds)
                logger.info(
                    "anthropic stream open failed (attempt %d/%d, sleep %.1fs): %s",
                    attempt, MAX_ATTEMPTS, delay, exc,
                )
                yield RetryingEvent(
                    attempt=attempt,
                    delay_seconds=delay,
                    error_message=str(exc),
                )
                await asyncio.sleep(delay)
        # Defensive — the loop above always exits via `break` (after a
        # successful __aenter__) or by raising. Use a real check instead
        # of `assert` so `python -O` can't strip it and silently NPE in
        # the iteration below.
        if stream is None or stream_ctx is None:
            raise LLMError("Anthropic stream open exhausted retries without raising")

        input_tokens = 0
        output_tokens = 0
        cache_read = 0
        cache_write = 0
        stop_reason: StopReason | None = None
        try:
            current_tool_id = None
            current_tool_name = None
            current_tool_input_json = ""

            async for event in stream:
                if event.type == "content_block_start":
                    if event.content_block.type == "tool_use":
                        current_tool_id = event.content_block.id
                        current_tool_name = event.content_block.name
                        current_tool_input_json = ""
                elif event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        yield TextEvent(text=event.delta.text)
                    elif event.delta.type == "input_json_delta":
                        current_tool_input_json += event.delta.partial_json
                elif event.type == "content_block_stop":
                    if current_tool_id and current_tool_name:
                        yield build_tool_use_event(
                            tool_id=current_tool_id,
                            tool_name=current_tool_name,
                            arguments=current_tool_input_json,
                            logger=logger,
                            context="Anthropic tool stream",
                        )
                        current_tool_id = None
                        current_tool_name = None
                        current_tool_input_json = ""
                elif event.type == "message_start" and hasattr(event, "message"):
                    usage = getattr(event.message, "usage", None)
                    if usage:
                        input_tokens = getattr(usage, "input_tokens", 0) or 0
                        cache_read, cache_write = self._extract_cache_usage(usage)
                elif event.type == "message_delta":
                    usage = getattr(event, "usage", None)
                    if usage:
                        output_tokens = getattr(usage, "output_tokens", 0) or 0
                    delta = getattr(event, "delta", None)
                    raw_stop = getattr(delta, "stop_reason", None) if delta else None
                    if raw_stop:
                        stop_reason = _STOP_MAP.get(raw_stop, StopReason.END_TURN)
        except LLMError:
            raise
        except Exception as exc:
            raise self._translate_error(exc)
        finally:
            # Forward the live exception (incl. CancelledError on disconnect)
            # to the SDK's context manager so it can clean up appropriately
            # — passing all-None would tell it "clean exit", which leaks
            # the underlying HTTP connection on cancel.
            exc_info = sys.exc_info()
            try:
                await stream_ctx.__aexit__(*exc_info)
            except Exception:
                logger.exception("Failed to close anthropic stream")

        duration = time.monotonic() - t0
        self._set_last_usage(Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        ))
        self._set_last_stop_reason(stop_reason)
        logger.debug(
            "LLM stream  model=%s  in=%d out=%d  cache_read=%d cache_write=%d  stop=%s  %.1fs",
            self.model, input_tokens, output_tokens,
            cache_read, cache_write, stop_reason, duration,
        )

    @staticmethod
    def _convert_messages(messages: list[Message]) -> list[dict]:
        """Convert provider-agnostic Messages to Anthropic wire format."""
        result = []
        for msg in messages:
            if msg.role == "user":
                result.append({"role": "user", "content": msg.text})
            elif msg.role == "assistant":
                blocks = []
                if msg.text:
                    blocks.append({"type": "text", "text": msg.text})
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        blocks.append({
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.input,
                        })
                if blocks:
                    result.append({"role": "assistant", "content": blocks})
            elif msg.role == "tool_result":
                result.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_use_id,
                            "content": msg.tool_content,
                        }
                    ],
                })

        # Merge consecutive user tool_result messages into a single message
        # (Anthropic requires strictly alternating user/assistant roles)
        merged: list[dict] = []
        for entry in result:
            if (entry["role"] == "user"
                    and isinstance(entry["content"], list)
                    and entry["content"][0].get("type") == "tool_result"
                    and merged
                    and merged[-1]["role"] == "user"
                    and isinstance(merged[-1]["content"], list)
                    and merged[-1]["content"][0].get("type") == "tool_result"):
                merged[-1]["content"].extend(entry["content"])
            else:
                merged.append(entry)
        return merged

    def _build_kwargs(
        self,
        messages: list[dict],
        tools: list[ToolDefinition] | None,
        system: str | None,
        tool_choice: ToolChoice | None = None,
    ) -> dict:
        kwargs = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "messages": self._add_message_cache_breakpoint(messages),
        }
        if system:
            # System prompt as a cached block — same content across every
            # call within a session, so caching it pays off after the
            # second turn (5-min ephemeral TTL).
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        if tools:
            kwargs["tools"] = [t.to_dict() for t in tools]
        if tool_choice:
            kwargs["tool_choice"] = _ANTHROPIC_TOOL_CHOICE[tool_choice]
        return kwargs

    @staticmethod
    def _add_message_cache_breakpoint(messages: list[dict]) -> list[dict]:
        """Mark the last block of the last message with cache_control.

        Anthropic's `cache_control: ephemeral` flags everything *before*
        the marker as cacheable. Putting it on the very last block caches
        the entire conversation history; later iterations within the
        same turn only pay full price for the newly appended tool_result
        blocks. Net win grows with conversation length.

        Safe-to-mutate copy: shallow-clones the last message and the
        block we change so callers' lists aren't disturbed.
        """
        if not messages:
            return messages
        last = dict(messages[-1])
        content = last.get("content")
        if isinstance(content, str):
            last["content"] = [
                {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
            ]
        elif isinstance(content, list) and content:
            new_content = list(content)
            last_block = dict(new_content[-1])
            last_block["cache_control"] = {"type": "ephemeral"}
            new_content[-1] = last_block
            last["content"] = new_content
        else:
            # Empty / unexpected shape — leave the request alone rather
            # than risk a 400 from a malformed cache_control marker.
            return messages
        return list(messages[:-1]) + [last]

    @staticmethod
    def _extract_cache_usage(usage) -> tuple[int, int]:
        """Pull (cache_read_input_tokens, cache_creation_input_tokens) when present."""
        if usage is None:
            return 0, 0
        return (
            int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        )

    @classmethod
    def _parse_response(cls, response) -> MessageResponse:
        content = []
        for block in response.content:
            if block.type == "text":
                content.append(TextEvent(text=block.text))
            elif block.type == "tool_use":
                content.append(ToolUseEvent(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))
        cache_read, cache_write = cls._extract_cache_usage(response.usage)
        return MessageResponse(
            content=content,
            stop_reason=_STOP_MAP.get(response.stop_reason, StopReason.END_TURN),
            usage=Usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
            ),
        )
