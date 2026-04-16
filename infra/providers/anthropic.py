"""Anthropic Claude provider implementation."""

import json
import logging
import os
import time
from typing import AsyncIterator

import anthropic

from infra.providers.base import (
    BaseLLMClient, LLMError, Message, MessageResponse, TextEvent, ToolUseEvent,
    StopReason, Usage, ToolDefinition,
)

logger = logging.getLogger(__name__)

_STOP_MAP: dict[str, StopReason] = {
    "end_turn": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
}


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude client with streaming support."""

    def __init__(self, model: str, *, max_output_tokens: int = 4096, api_key: str | None = None):
        super().__init__(model, max_output_tokens=max_output_tokens)
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
        )

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def _translate_error(self, exc: Exception) -> LLMError:
        if isinstance(exc, anthropic.AuthenticationError):
            return LLMError("Invalid API key — check ANTHROPIC_API_KEY")
        if isinstance(exc, anthropic.RateLimitError):
            return LLMError("Rate limited by Anthropic — retry shortly")
        if isinstance(exc, anthropic.APIError):
            return LLMError(f"Anthropic API error: {exc.message}")
        return LLMError(f"Anthropic error: {exc}")

    async def create_message(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
    ) -> MessageResponse:
        kwargs = self._build_kwargs(
            self._convert_messages(messages), tools, system,
        )
        t0 = time.monotonic()
        async with self._wrap_api_errors():
            response = await self._client.messages.create(**kwargs)
        duration = time.monotonic() - t0
        parsed = self._parse_response(response)
        self._set_last_usage(parsed.usage)
        logger.debug(
            "LLM create  model=%s  in=%d out=%d  %.1fs",
            self.model, parsed.usage.input_tokens,
            parsed.usage.output_tokens, duration,
        )
        return parsed

    async def stream_message(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[TextEvent | ToolUseEvent]:
        kwargs = self._build_kwargs(
            self._convert_messages(messages), tools, system,
        )
        t0 = time.monotonic()
        async with self._wrap_api_errors():
            stream_ctx = self._client.messages.stream(**kwargs)

        input_tokens = 0
        output_tokens = 0
        try:
            async with stream_ctx as stream:
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
                            try:
                                tool_input = json.loads(current_tool_input_json) if current_tool_input_json else {}
                            except json.JSONDecodeError:
                                logger.warning("Failed to parse tool input JSON: %s", current_tool_input_json[:200])
                                tool_input = {}
                            yield ToolUseEvent(
                                id=current_tool_id,
                                name=current_tool_name,
                                input=tool_input,
                            )
                            current_tool_id = None
                            current_tool_name = None
                            current_tool_input_json = ""
                    elif event.type == "message_start" and hasattr(event, "message"):
                        usage = getattr(event.message, "usage", None)
                        if usage:
                            input_tokens = getattr(usage, "input_tokens", 0) or 0
                    elif event.type == "message_delta":
                        usage = getattr(event, "usage", None)
                        if usage:
                            output_tokens = getattr(usage, "output_tokens", 0) or 0
        except LLMError:
            raise
        except Exception as exc:
            raise self._translate_error(exc)

        duration = time.monotonic() - t0
        self._set_last_usage(Usage(input_tokens=input_tokens, output_tokens=output_tokens))
        logger.debug(
            "LLM stream  model=%s  in=%d out=%d  %.1fs",
            self.model, input_tokens, output_tokens, duration,
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
    ) -> dict:
        kwargs = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [t.to_dict() for t in tools]
        return kwargs

    @staticmethod
    def _parse_response(response) -> MessageResponse:
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
        return MessageResponse(
            content=content,
            stop_reason=_STOP_MAP.get(response.stop_reason, StopReason.END_TURN),
            usage=Usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
        )
