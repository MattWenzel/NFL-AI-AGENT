"""OpenAI Codex (ChatGPT) provider — Responses API over OAuth.

Posts to `https://chatgpt.com/backend-api/codex/responses` with a bearer
access token issued via `infra/codex_oauth.py`. Unlike the standard OpenAI
provider, this talks to OpenAI's internal Responses API (the one Codex CLI
uses), not the public Chat Completions endpoint — so the request body,
streaming event names, and tool-call shape all differ.

Token refresh happens one layer up (in `api/dependencies.py`), so the
client here only ever sees a live access token string passed as `api_key`.
"""

from __future__ import annotations

import copy
import json
import logging
import time
import uuid
from typing import AsyncIterator

import httpx

from infra.codex_oauth import CodexOAuthError, decode_account_id
from infra.providers.base import (
    BaseLLMClient,
    LLMError,
    Message,
    MessageResponse,
    StopReason,
    TextEvent,
    ToolChoice,
    ToolDefinition,
    ToolUseEvent,
    Usage,
)

logger = logging.getLogger(__name__)

CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"


class OpenAICodexClient(BaseLLMClient):
    """Codex Responses API client. Streams SSE, emits canonical TextEvent /
    ToolUseEvent, and supports tool-use loops via create_message."""

    def __init__(
        self,
        model: str,
        *,
        max_output_tokens: int = 16384,
        api_key: str | None = None,
    ):
        super().__init__(model, max_output_tokens=max_output_tokens)
        if not api_key:
            raise LLMError("OpenAI Codex requires an OAuth access token — connect ChatGPT in Settings.")
        self._access_token = api_key
        try:
            self._account_id = decode_account_id(api_key)
        except CodexOAuthError as exc:
            raise LLMError(str(exc))
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0))

    @property
    def provider_name(self) -> str:
        return "openai-codex"

    async def aclose(self) -> None:
        await self._http.aclose()

    def _translate_error(self, exc: Exception) -> LLMError:
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status in (401, 403):
                return LLMError("ChatGPT OAuth token rejected — reconnect in Settings.")
            if status == 429:
                return LLMError("Rate limited by ChatGPT — retry shortly.")
            body = (exc.response.text or "")[:300]
            return LLMError(f"Codex API error (HTTP {status}): {body}")
        if isinstance(exc, httpx.RequestError):
            return LLMError(f"Codex request failed: {exc}")
        return LLMError(f"Codex error: {exc}")

    # ---------------- public API ----------------

    async def create_message(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        model: str | None = None,
    ) -> MessageResponse:
        """Send one Codex request and return the full response.

        Codex is always streamed; this helper collects the stream into a
        MessageResponse to match the `BaseLLMClient.create_message` contract.
        """
        content: list = []
        text_buf: list[str] = []
        async for event in self._run_stream(messages, tools, system, model=model):
            if isinstance(event, TextEvent):
                text_buf.append(event.text)
            elif isinstance(event, ToolUseEvent):
                if text_buf:
                    content.append(TextEvent(text="".join(text_buf)))
                    text_buf.clear()
                content.append(event)
        if text_buf:
            content.append(TextEvent(text="".join(text_buf)))
        return MessageResponse(
            content=content,
            stop_reason=self.last_stop_reason or StopReason.END_TURN,
            usage=self.last_usage,
        )

    async def stream_message(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[TextEvent | ToolUseEvent]:
        async for event in self._run_stream(messages, tools, system, tool_choice=tool_choice):
            yield event

    # ---------------- streaming core ----------------

    async def _run_stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        system: str | None,
        *,
        model: str | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[TextEvent | ToolUseEvent]:
        body = self._build_body(messages, tools, system, model=model, tool_choice=tool_choice)
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "chatgpt-account-id": self._account_id,
            "OpenAI-Beta": "responses=experimental",
            "originator": "pi",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        t0 = time.monotonic()
        # Per-stream assembly state
        item_to_call: dict[str, str] = {}  # ephemeral item.id → stable call_id
        calls: dict[str, dict] = {}        # call_id → {name, arguments, emitted}
        pending_text: list[str] = []
        usage = Usage()
        stop_reason: StopReason | None = None

        try:
            async with self._http.stream("POST", CODEX_URL, headers=headers, json=body) as resp:
                if resp.status_code >= 400:
                    payload = await resp.aread()
                    raise httpx.HTTPStatusError(
                        f"Codex returned {resp.status_code}",
                        request=resp.request,
                        response=httpx.Response(
                            resp.status_code,
                            content=payload,
                            request=resp.request,
                        ),
                    )
                async for raw_event in self._iter_sse(resp):
                    etype = raw_event.get("type")
                    if etype == "error":
                        msg = (raw_event.get("error") or {}).get("message") or "Codex stream error"
                        raise LLMError(msg)

                    if etype == "response.output_text.delta":
                        delta = raw_event.get("delta") or ""
                        if delta:
                            pending_text.append(delta)
                            yield TextEvent(text=delta)

                    elif etype == "response.output_item.added":
                        item = raw_event.get("item") or {}
                        if item.get("type") == "function_call":
                            call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:12]}"
                            item_id = item.get("id")
                            if item_id:
                                item_to_call[item_id] = call_id
                            calls.setdefault(call_id, {
                                "name": item.get("name", ""),
                                "arguments": "",
                                "emitted": False,
                            })

                    elif etype == "response.function_call_arguments.delta":
                        call_id = self._resolve_call_id(raw_event, item_to_call)
                        delta = raw_event.get("delta") or ""
                        if call_id:
                            slot = calls.setdefault(call_id, {"name": "", "arguments": "", "emitted": False})
                            slot["arguments"] += delta

                    elif etype == "response.function_call_arguments.done":
                        call_id = self._resolve_call_id(raw_event, item_to_call)
                        final_args = raw_event.get("arguments")
                        if call_id:
                            slot = calls.setdefault(call_id, {"name": "", "arguments": "", "emitted": False})
                            if final_args is not None:
                                slot["arguments"] = final_args
                            # Wait for output_item.done to fill in name if missing; emit if we have both.
                            if slot["name"] and not slot["emitted"]:
                                slot["emitted"] = True
                                yield self._assemble_tool_event(call_id, slot)

                    elif etype == "response.output_item.done":
                        item = raw_event.get("item") or {}
                        if item.get("type") == "function_call":
                            call_id = item.get("call_id") or item_to_call.get(item.get("id", ""))
                            if call_id:
                                slot = calls.setdefault(call_id, {"name": "", "arguments": "", "emitted": False})
                                if item.get("name"):
                                    slot["name"] = item["name"]
                                if item.get("arguments") is not None:
                                    slot["arguments"] = item["arguments"]
                                if slot["name"] and not slot["emitted"]:
                                    slot["emitted"] = True
                                    yield self._assemble_tool_event(call_id, slot)

                    elif etype in ("response.done", "response.completed"):
                        # `response.output` is empirically always [] on this endpoint —
                        # streaming events (output_item.added / function_call_arguments.done /
                        # output_item.done) are the sole source of truth for tool calls.
                        response = raw_event.get("response") or {}
                        usage = self._parse_usage(response.get("usage"))
                        stop_reason = self._derive_stop_reason(calls)
        except LLMError:
            raise
        except httpx.HTTPStatusError as exc:
            raise self._translate_error(exc)
        except Exception as exc:
            raise self._translate_error(exc)

        # Fallback: if the stream terminated on `[DONE]` before emitting
        # response.done/response.completed, derive stop_reason from the
        # calls dict so an emitted tool isn't silently downgraded to END_TURN.
        if stop_reason is None:
            stop_reason = self._derive_stop_reason(calls)
        self._set_last_usage(usage)
        self._set_last_stop_reason(stop_reason or StopReason.END_TURN)
        duration = time.monotonic() - t0
        logger.debug(
            "Codex stream  model=%s  in=%d out=%d  stop=%s  %.1fs",
            self.model, usage.input_tokens, usage.output_tokens, stop_reason, duration,
        )

    @staticmethod
    def _resolve_call_id(event: dict, item_to_call: dict[str, str]) -> str | None:
        explicit = event.get("call_id")
        if explicit:
            return explicit
        item_id = event.get("item_id")
        if item_id and item_id in item_to_call:
            return item_to_call[item_id]
        # Fall back to using item_id as the call_id (some events may omit the mapping).
        return item_id

    @staticmethod
    def _assemble_tool_event(call_id: str, slot: dict) -> ToolUseEvent:
        args_str = slot.get("arguments") or ""
        try:
            tool_input = json.loads(args_str) if args_str else {}
        except json.JSONDecodeError:
            logger.warning("Codex tool %s: invalid JSON arguments %r", slot.get("name"), args_str[:200])
            tool_input = {}
        return ToolUseEvent(id=call_id, name=slot.get("name", ""), input=tool_input)

    @staticmethod
    def _parse_usage(usage: dict | None) -> Usage:
        if not usage:
            return Usage()
        input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
        output_tokens = usage.get("output_tokens") or usage.get("completion_tokens") or 0
        return Usage(input_tokens=int(input_tokens), output_tokens=int(output_tokens))

    @staticmethod
    def _derive_stop_reason(calls: dict) -> StopReason:
        """Codex omits a finish_reason field. If the stream emitted any
        function_call, the runtime needs to execute it — surface TOOL_USE so
        the agent loop keeps going."""
        if any(slot.get("emitted") for slot in calls.values()):
            return StopReason.TOOL_USE
        return StopReason.END_TURN

    # ---------------- SSE parser ----------------

    @staticmethod
    async def _iter_sse(resp: httpx.Response):
        """Minimal SSE parser: yields each JSON-decoded `data:` payload.

        Codex's stream uses only `data:` lines (no explicit `event:` names —
        the event type is encoded in the payload's `type` field). Terminates
        on `data: [DONE]`.
        """
        buf: list[str] = []
        async for line in resp.aiter_lines():
            if line == "":
                if not buf:
                    continue
                data = "\n".join(buf)
                buf = []
                if data.strip() == "[DONE]":
                    return
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    logger.warning("Codex SSE: bad JSON payload %r", data[:200])
                continue
            if line.startswith(":"):
                # Comment / keepalive
                continue
            if line.startswith("data:"):
                buf.append(line[5:].lstrip())

    # ---------------- request body ----------------

    def _build_body(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        system: str | None,
        *,
        model: str | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> dict:
        body = {
            "model": model or self.model,
            "store": False,
            "stream": True,
            "input": self._convert_messages(messages),
            "tool_choice": tool_choice or "auto",
            "parallel_tool_calls": True,
        }
        if system:
            body["instructions"] = system
        if tools:
            body["tools"] = [self._convert_tool(t) for t in tools]
        return body

    @staticmethod
    def _convert_messages(messages: list[Message]) -> list[dict]:
        """Map canonical Messages to Codex Responses `input` array.

        Key difference from Chat Completions: assistant turns with both
        text and tool calls split into *separate* array items. Tool
        results are `function_call_output` entries, not role=tool messages.
        """
        out: list[dict] = []
        for msg in messages:
            if msg.role == "user":
                if msg.text:
                    out.append({
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": msg.text}],
                    })
            elif msg.role == "assistant":
                if msg.text:
                    out.append({
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": msg.text}],
                    })
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        out.append({
                            "type": "function_call",
                            "call_id": tc.id,
                            "name": tc.name,
                            "arguments": json.dumps(tc.input),
                        })
            elif msg.role == "tool_result":
                out.append({
                    "type": "function_call_output",
                    "call_id": msg.tool_use_id,
                    "output": msg.tool_content or "",
                })
        return out

    # ---------------- tool schema ----------------

    @classmethod
    def _convert_tool(cls, tool: ToolDefinition) -> dict:
        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": cls._make_strict_schema(tool.input_schema),
            "strict": True,
        }

    @classmethod
    def _make_strict_schema(cls, schema: dict) -> dict:
        """Copy + transform a JSON Schema into Codex's strict-mode dialect.

        Rules (applied recursively):
          - Every `object` gets `additionalProperties: false`.
          - Every property in an object appears in `required` — optional
            properties are made nullable instead of being dropped from
            `required`.
        """
        cloned = copy.deepcopy(schema) if schema else {"type": "object", "properties": {}}
        cls._strictify(cloned)
        return cloned

    @classmethod
    def _strictify(cls, node) -> None:
        if isinstance(node, dict):
            node_type = node.get("type")
            if node_type == "object" or "properties" in node:
                node["additionalProperties"] = False
                props = node.get("properties") or {}
                current_required = set(node.get("required") or [])
                all_props = list(props.keys())
                # Optional properties → nullable, then mark required.
                for name, prop_schema in props.items():
                    if name not in current_required:
                        cls._make_nullable(prop_schema)
                if all_props:
                    node["required"] = all_props
                for prop_schema in props.values():
                    cls._strictify(prop_schema)
            # Recurse into container keywords.
            for key in ("items", "additionalProperties", "not"):
                if key in node and isinstance(node[key], dict):
                    cls._strictify(node[key])
            for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
                if key in node and isinstance(node[key], list):
                    for sub in node[key]:
                        cls._strictify(sub)
        elif isinstance(node, list):
            for item in node:
                cls._strictify(item)

    @staticmethod
    def _make_nullable(prop: dict) -> None:
        """Transform an optional property schema into a nullable variant."""
        if "type" in prop and isinstance(prop["type"], str) and prop["type"] != "null":
            prop["type"] = [prop["type"], "null"]
        elif "type" in prop and isinstance(prop["type"], list) and "null" not in prop["type"]:
            prop["type"] = [*prop["type"], "null"]
        elif "anyOf" in prop and isinstance(prop["anyOf"], list):
            if not any(isinstance(v, dict) and v.get("type") == "null" for v in prop["anyOf"]):
                prop["anyOf"] = [*prop["anyOf"], {"type": "null"}]
        elif "oneOf" in prop and isinstance(prop["oneOf"], list):
            if not any(isinstance(v, dict) and v.get("type") == "null" for v in prop["oneOf"]):
                prop["oneOf"] = [*prop["oneOf"], {"type": "null"}]
        else:
            # Fallback: wrap the whole schema in anyOf with null.
            original = {k: v for k, v in prop.items()}
            prop.clear()
            prop["anyOf"] = [original, {"type": "null"}]
