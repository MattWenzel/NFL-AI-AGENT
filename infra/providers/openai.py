"""OpenAI provider implementation."""

import json
import logging
import os
import time
from typing import AsyncIterator

from infra.providers.base import (
    BaseLLMClient, LLMError, Message, MessageResponse, TextEvent, ToolUseEvent,
    StopReason, Usage, ToolDefinition,
)

logger = logging.getLogger(__name__)

# Import openai at module level (ImportError caught by registry)
import openai

_STOP_MAP: dict[str, StopReason] = {
    "stop": StopReason.END_TURN,
    "tool_calls": StopReason.TOOL_USE,
    "length": StopReason.MAX_TOKENS,
}


class OpenAIClient(BaseLLMClient):
    """OpenAI client with streaming and tool support."""

    def __init__(self, model: str, *, max_output_tokens: int = 16384, api_key: str | None = None):
        super().__init__(model, max_output_tokens=max_output_tokens)
        self._client = openai.AsyncOpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
        )

    @property
    def provider_name(self) -> str:
        return "openai"

    def _translate_error(self, exc: Exception) -> LLMError:
        if isinstance(exc, openai.AuthenticationError):
            return LLMError("Invalid OpenAI API key — update it in Settings")
        if isinstance(exc, openai.RateLimitError):
            return LLMError("Rate limited by OpenAI — retry shortly")
        if isinstance(exc, openai.APIError):
            return LLMError(f"OpenAI API error: {exc.message}")
        return LLMError(f"OpenAI error: {exc}")

    async def create_message(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        model: str | None = None,
    ) -> MessageResponse:
        kwargs = self._build_kwargs(
            self._convert_messages(messages),
            self._convert_tools(tools),
            system,
        )
        if model:
            kwargs["model"] = model
        t0 = time.monotonic()
        async with self._wrap_api_errors():
            response = await self._client.chat.completions.create(**kwargs)
        duration = time.monotonic() - t0
        parsed = self._parse_response(response)
        self._set_last_usage(parsed.usage)
        self._set_last_stop_reason(parsed.stop_reason)
        logger.debug(
            "LLM create  model=%s  in=%d out=%d  stop=%s  %.1fs",
            kwargs["model"], parsed.usage.input_tokens,
            parsed.usage.output_tokens, parsed.stop_reason, duration,
        )
        return parsed

    async def stream_message(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[TextEvent | ToolUseEvent]:
        kwargs = self._build_kwargs(
            self._convert_messages(messages),
            self._convert_tools(tools),
            system,
        )
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        t0 = time.monotonic()
        async with self._wrap_api_errors():
            stream = await self._client.chat.completions.create(**kwargs)

        # Track tool call accumulation across chunks
        tool_calls_acc: dict[int, dict] = {}  # index -> {id, name, arguments}
        input_tokens = 0
        output_tokens = 0
        finish_reason: str | None = None

        try:
            async for chunk in stream:
                # Track usage from the final chunk (sent when include_usage=True)
                if chunk.usage:
                    input_tokens = chunk.usage.prompt_tokens or 0
                    output_tokens = chunk.usage.completion_tokens or 0

                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta:
                    continue

                # Text content
                if delta.content:
                    yield TextEvent(text=delta.content)

                # Tool calls (streamed incrementally by index)
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": "",
                                "name": "",
                                "arguments": "",
                            }
                        acc = tool_calls_acc[idx]
                        if tc_delta.id:
                            acc["id"] = tc_delta.id
                        if tc_delta.function and tc_delta.function.name:
                            acc["name"] = tc_delta.function.name
                        if tc_delta.function and tc_delta.function.arguments:
                            acc["arguments"] += tc_delta.function.arguments

                # Check for finish reason to emit completed tool calls
                finish = chunk.choices[0].finish_reason if chunk.choices else None
                if finish:
                    finish_reason = finish
                if finish == "tool_calls":
                    for event in self._emit_accumulated_tools(tool_calls_acc):
                        yield event
        except LLMError:
            raise
        except Exception as exc:
            raise self._translate_error(exc)

        # Emit any remaining tool calls (e.g. finish_reason="length" or missing finish chunk)
        if tool_calls_acc:
            logger.warning(
                "%d tool call(s) accumulated without finish_reason='tool_calls'",
                len(tool_calls_acc),
            )
            for event in self._emit_accumulated_tools(tool_calls_acc):
                yield event

        duration = time.monotonic() - t0
        self._set_last_usage(Usage(input_tokens=input_tokens, output_tokens=output_tokens))
        self._set_last_stop_reason(_STOP_MAP.get(finish_reason) if finish_reason else None)
        logger.debug(
            "LLM stream  model=%s  in=%d out=%d  stop=%s  %.1fs",
            self.model, input_tokens, output_tokens, finish_reason, duration,
        )

    @staticmethod
    def _emit_accumulated_tools(tool_calls_acc: dict) -> list[ToolUseEvent]:
        """Parse and return accumulated tool calls, then clear the accumulator."""
        events = []
        for idx in sorted(tool_calls_acc.keys()):
            acc = tool_calls_acc[idx]
            if acc["id"] and acc["name"]:
                try:
                    tool_input = json.loads(acc["arguments"]) if acc["arguments"] else {}
                except json.JSONDecodeError:
                    logger.warning("Failed to parse tool input JSON: %s", acc["arguments"][:200])
                    tool_input = {}
                events.append(ToolUseEvent(id=acc["id"], name=acc["name"], input=tool_input))
            else:
                logger.warning("Dropping incomplete tool call at index %d: id=%r name=%r", idx, acc["id"], acc["name"])
        tool_calls_acc.clear()
        return events

    @staticmethod
    def _convert_messages(messages: list[Message]) -> list[dict]:
        """Convert provider-agnostic Messages to OpenAI wire format.

        `system` is prepended by `_build_kwargs` (matching how Anthropic
        routes the system prompt) so this helper only handles the
        conversation body.
        """
        result = []
        for msg in messages:
            if msg.role == "user":
                result.append({"role": "user", "content": msg.text})
            elif msg.role == "assistant":
                out = {"role": "assistant"}
                if msg.text:
                    out["content"] = msg.text
                if msg.tool_calls:
                    out["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.input),
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                result.append(out)
            elif msg.role == "tool_result":
                result.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_use_id,
                    "content": msg.tool_content,
                })
        return result

    @staticmethod
    def _convert_tools(tools: list[ToolDefinition] | None) -> list[dict] | None:
        """Convert ToolDefinition list to OpenAI function format."""
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

    def _build_kwargs(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        system: str | None,
    ) -> dict:
        if system:
            messages = [{"role": "system", "content": system}] + messages
        kwargs = {
            "model": self.model,
            "max_completion_tokens": self.max_output_tokens,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        return kwargs

    @staticmethod
    def _parse_response(response) -> MessageResponse:
        choice = response.choices[0]
        content = []

        if choice.message.content:
            content.append(TextEvent(text=choice.message.content))

        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                try:
                    tool_input = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    logger.warning("Failed to parse tool input JSON: %s", tc.function.arguments[:200])
                    tool_input = {}
                content.append(ToolUseEvent(
                    id=tc.id,
                    name=tc.function.name,
                    input=tool_input,
                ))

        usage = Usage()
        if response.usage:
            usage = Usage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
            )

        return MessageResponse(
            content=content,
            stop_reason=_STOP_MAP.get(choice.finish_reason or "stop", StopReason.END_TURN),
            usage=usage,
        )
