"""Codex (ChatGPT Responses API) provider — OAuth, SSE, strict tool schemas.

Authenticates via `CodexAuth` (OAuth+PKCE tokens on disk). Posts to
`https://chatgpt.com/backend-api/codex/responses` using raw httpx; parses
the custom SSE event stream in-line. Does not use the OpenAI SDK because
the Responses API wire shape differs from chat-completions.

See the `codex-oauth` skill for the request contract and event reference.
"""

from __future__ import annotations

import copy
import json
import logging
from typing import AsyncIterator, Iterable

import httpx

from agent.oauth.codex_auth import CodexAuth, CodexAuthError
from agent.providers.base import (
    BaseLLMClient,
    LLMError,
    Message,
    MessageResponse,
    StopReason,
    TextEvent,
    ToolDefinition,
    ToolUseEvent,
    Usage,
)
from agent.runtime_store import safe_load_tool_input

logger = logging.getLogger(__name__)

CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
SSE_TERMINATOR = "[DONE]"


# ---------- strict schema conversion ----------

_JSON_SIMPLE_TYPES = {"string", "integer", "number", "boolean", "array", "object", "null"}


def _strictify_schema(schema: dict | None) -> dict:
    """Recursively rewrite a JSON Schema to satisfy Codex strict mode.

    Rules (from the codex-oauth skill):
      1. Every `object` must have `additionalProperties: false`.
      2. Every property must be listed in `required`.
      3. Originally-optional properties are made nullable instead of omitted.

    The input schema is not mutated.
    """
    if not schema:
        return {"type": "object", "additionalProperties": False, "properties": {}, "required": []}
    node = copy.deepcopy(schema)
    return _strictify_node(node, originally_required=True)


def _strictify_node(node: dict, *, originally_required: bool) -> dict:
    # anyOf / oneOf / allOf — recurse into each variant
    for combiner in ("anyOf", "oneOf", "allOf"):
        if combiner in node and isinstance(node[combiner], list):
            node[combiner] = [
                _strictify_node(variant, originally_required=True)
                for variant in node[combiner]
                if isinstance(variant, dict)
            ]

    node_type = node.get("type")

    if node_type == "object" or "properties" in node:
        props = node.get("properties") or {}
        original_required = set(node.get("required") or [])
        new_props: dict[str, dict] = {}
        for name, child in props.items():
            if not isinstance(child, dict):
                continue
            was_required = name in original_required
            strict_child = _strictify_node(child, originally_required=was_required)
            if not was_required:
                strict_child = _make_nullable(strict_child)
            new_props[name] = strict_child
        node["properties"] = new_props
        node["required"] = list(new_props.keys())
        node["additionalProperties"] = False
        if "type" not in node:
            node["type"] = "object"

    elif node_type == "array":
        items = node.get("items")
        if isinstance(items, dict):
            node["items"] = _strictify_node(items, originally_required=True)

    return node


def _make_nullable(schema: dict) -> dict:
    """Return a schema variant that permits null."""
    # anyOf / oneOf — append a null variant if not already present
    for combiner in ("anyOf", "oneOf"):
        if combiner in schema:
            variants = schema[combiner]
            if any(isinstance(v, dict) and v.get("type") == "null" for v in variants):
                return schema
            variants.append({"type": "null"})
            return schema

    t = schema.get("type")
    if isinstance(t, str):
        if t == "null":
            return schema
        schema["type"] = [t, "null"]
        return schema
    if isinstance(t, list):
        if "null" not in t:
            t.append("null")
            schema["type"] = t
        return schema

    # No simple type — wrap in anyOf as a fallback.
    return {"anyOf": [schema, {"type": "null"}]}


# ---------- message + tool conversion ----------

def _convert_messages(messages: list[Message]) -> list[dict]:
    """Canonical Message list → Codex `input` array (assistant text/tool are separate items)."""
    out: list[dict] = []
    for msg in messages:
        if msg.role == "user":
            out.append({
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": msg.text or ""}],
            })
        elif msg.role == "assistant":
            if msg.text:
                out.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": msg.text}],
                })
            for call in msg.tool_calls or []:
                out.append({
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": json.dumps(call.input or {}, separators=(",", ":"), sort_keys=True),
                })
        elif msg.role == "tool_result":
            out.append({
                "type": "function_call_output",
                "call_id": msg.tool_use_id or "",
                "output": msg.tool_content or "",
            })
        else:
            logger.warning("Skipping message with unknown role: %s", msg.role)
    return out


def _convert_tools(tools: Iterable[ToolDefinition] | None) -> list[dict]:
    if not tools:
        return []
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": _strictify_schema(tool.input_schema),
            "strict": True,
        }
        for tool in tools
    ]


# ---------- SSE parsing ----------

def iter_sse_events(lines: Iterable[str]) -> Iterable[dict]:
    """Parse SSE text lines into a stream of JSON event payloads.

    The Codex stream uses `data: <json>` lines separated by blank lines,
    and a terminating `data: [DONE]`. We collect multi-line `data:` chunks
    per event (concatenated with `\\n` per SSE convention), parse JSON,
    and yield the decoded dict. Lines we don't recognize are ignored.
    """
    buffer: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if line == "":
            if buffer:
                payload = "\n".join(buffer)
                buffer.clear()
                if payload == SSE_TERMINATOR:
                    return
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    logger.warning("Dropping malformed SSE payload: %s", payload[:200])
            continue
        if line.startswith(":"):  # comment / heartbeat
            continue
        if line.startswith("data:"):
            buffer.append(line[5:].lstrip())
        # `event:`, `id:`, `retry:` are ignored — payload `type` field is authoritative.
    if buffer:
        payload = "\n".join(buffer)
        if payload != SSE_TERMINATOR:
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                logger.warning("Dropping trailing malformed SSE payload: %s", payload[:200])


# ---------- CodexClient ----------

class CodexClient(BaseLLMClient):
    """Codex provider client. Authenticates via OAuth; streams via raw httpx."""

    def __init__(
        self,
        model: str,
        *,
        max_output_tokens: int = 8192,
        auth: CodexAuth,
        http_client: httpx.AsyncClient | None = None,
    ):
        super().__init__(model, max_output_tokens=max_output_tokens)
        self._auth = auth
        self._http = http_client

    @property
    def provider_name(self) -> str:
        return "codex"

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0))
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        await self._auth.aclose()

    # ---- public API (BaseLLMClient contract) ----

    async def create_message(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
    ) -> MessageResponse:
        content: list = []
        text_parts: list[str] = []
        stop_reason = StopReason.END_TURN
        async for item in self._stream_parsed(messages, tools, system):
            kind = item[0]
            if kind == "text":
                text_parts.append(item[1])
            elif kind == "tool_use":
                content.append(item[1])
                stop_reason = StopReason.TOOL_USE
            elif kind == "usage":
                self._set_last_usage(item[1])
            elif kind == "stop_reason":
                stop_reason = item[1]
        if text_parts:
            content.insert(0, TextEvent(text="".join(text_parts)))
        return MessageResponse(content=content, stop_reason=stop_reason, usage=self.last_usage)

    async def stream_message(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[TextEvent | ToolUseEvent]:
        async for item in self._stream_parsed(messages, tools, system):
            kind = item[0]
            if kind == "text":
                yield TextEvent(text=item[1])
            elif kind == "tool_use":
                yield item[1]
            elif kind == "usage":
                self._set_last_usage(item[1])

    def _translate_error(self, exc: Exception) -> LLMError:
        if isinstance(exc, CodexAuthError):
            return LLMError(f"Codex authentication: {exc}")
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            try:
                body = exc.response.json()
                msg = body.get("error", {}).get("message") if isinstance(body, dict) else None
            except ValueError:
                msg = None
            detail = msg or exc.response.text[:300] or "no body"
            if status == 401:
                return LLMError(f"Codex auth rejected (401) — re-authenticate: {detail}")
            if status == 429:
                return LLMError(f"Codex rate limited (429): {detail}")
            return LLMError(f"Codex API error ({status}): {detail}")
        if isinstance(exc, httpx.RequestError):
            return LLMError(f"Codex network error: {exc}")
        return LLMError(f"Codex error: {exc}")

    # ---- internals ----

    async def _stream_parsed(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        system: str | None,
    ):
        """Post the request and yield tagged events: ('text', str), ('tool_use', ToolUseEvent),
        ('usage', Usage), ('stop_reason', StopReason). Errors are translated via _wrap_api_errors."""
        body = {
            "model": self.model,
            "store": False,
            "stream": True,
            "input": _convert_messages(messages),
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "tools": _convert_tools(tools),
        }
        if system:
            body["instructions"] = system

        access_token, account_id = await self._auth.get_valid_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "chatgpt-account-id": account_id,
            "OpenAI-Beta": "responses=experimental",
            "originator": "pi",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        async with self._wrap_api_errors():
            client = await self._client()
            async with client.stream("POST", CODEX_RESPONSES_URL, json=body, headers=headers) as resp:
                if resp.status_code >= 400:
                    raw = await resp.aread()
                    resp_for_error = httpx.Response(
                        resp.status_code,
                        content=raw,
                        request=resp.request,
                        headers=resp.headers,
                    )
                    raise httpx.HTTPStatusError(
                        f"Codex HTTP {resp.status_code}", request=resp.request, response=resp_for_error,
                    )
                line_iter = resp.aiter_lines()
                async for item in _parse_codex_stream(line_iter):
                    yield item


# ---------- stream state machine ----------

async def _parse_codex_stream(line_iter) -> AsyncIterator[tuple]:
    """Drive `iter_sse_events` over an async line iterator and convert to tagged events."""
    # Buffer lines into memory-cheap chunks; iter_sse_events is synchronous,
    # so we batch lines until a blank line then feed it.
    buffer: list[str] = []
    state = _StreamState()
    async for raw_line in line_iter:
        line = raw_line.rstrip("\r\n")
        buffer.append(line)
        if line != "":
            continue
        # End of an event; feed what we have to the sync parser.
        for event in iter_sse_events(buffer):
            for tagged in state.handle(event):
                yield tagged
        buffer.clear()
    if buffer:
        for event in iter_sse_events(buffer + [""]):
            for tagged in state.handle(event):
                yield tagged
    for tagged in state.finish():
        yield tagged


class _StreamState:
    """Accumulates tool calls across streaming events and emits tagged tuples."""

    def __init__(self) -> None:
        self.item_to_call: dict[str, str] = {}
        self.tool_calls: dict[str, dict] = {}  # call_id → {"name", "arguments", "emitted"}
        self.stop_reason: StopReason | None = None
        self.usage: Usage | None = None
        self.fell_through_emission_ids: set[str] = set()

    def handle(self, event: dict) -> Iterable[tuple]:
        etype = event.get("type")
        if etype == "error":
            message = (event.get("error") or {}).get("message") or "Codex stream error"
            raise LLMError(message)

        if etype == "response.output_text.delta":
            delta = event.get("delta") or ""
            if delta:
                yield ("text", delta)
            return

        if etype == "response.output_item.added":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                item_id = item.get("id")
                call_id = item.get("call_id") or item_id
                if item_id and call_id:
                    self.item_to_call[item_id] = call_id
                if call_id:
                    entry = self.tool_calls.setdefault(
                        call_id, {"name": item.get("name") or "", "arguments": "", "emitted": False}
                    )
                    if item.get("name") and not entry["name"]:
                        entry["name"] = item["name"]
            return

        if etype == "response.function_call_arguments.delta":
            call_id = self._resolve_call_id(event)
            if not call_id:
                return
            entry = self.tool_calls.setdefault(call_id, {"name": "", "arguments": "", "emitted": False})
            entry["arguments"] += event.get("delta") or ""
            return

        if etype == "response.function_call_arguments.done":
            call_id = self._resolve_call_id(event)
            if not call_id:
                return
            entry = self.tool_calls.setdefault(call_id, {"name": "", "arguments": "", "emitted": False})
            if event.get("arguments") is not None:
                entry["arguments"] = event["arguments"]  # authoritative
            yield from self._try_emit(call_id, entry)
            return

        if etype == "response.output_item.done":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                call_id = item.get("call_id") or self.item_to_call.get(item.get("id"))
                if call_id:
                    entry = self.tool_calls.setdefault(
                        call_id, {"name": item.get("name") or "", "arguments": "", "emitted": False}
                    )
                    if item.get("name"):
                        entry["name"] = item["name"]
                    if item.get("arguments") is not None:
                        entry["arguments"] = item["arguments"]
                    yield from self._try_emit(call_id, entry)
            return

        if etype in ("response.done", "response.completed"):
            response = event.get("response") or {}
            usage = response.get("usage") or {}
            if usage:
                self.usage = Usage(
                    input_tokens=int(usage.get("input_tokens") or 0),
                    output_tokens=int(usage.get("output_tokens") or 0),
                )
            # Fallback: pick up any function_calls present in response.output
            # that we never emitted via the delta stream.
            for item in response.get("output") or []:
                if item.get("type") == "function_call":
                    call_id = item.get("call_id") or item.get("id")
                    if not call_id:
                        continue
                    entry = self.tool_calls.setdefault(
                        call_id,
                        {"name": item.get("name") or "", "arguments": "", "emitted": False},
                    )
                    if item.get("name"):
                        entry["name"] = item["name"]
                    if item.get("arguments") is not None:
                        entry["arguments"] = item["arguments"]
                    yield from self._try_emit(call_id, entry)
            # Stop reason: if any tool call was produced, it's TOOL_USE.
            if any(entry["emitted"] for entry in self.tool_calls.values()):
                self.stop_reason = StopReason.TOOL_USE
            else:
                self.stop_reason = StopReason.END_TURN
            return

    def finish(self) -> Iterable[tuple]:
        if self.usage is not None:
            yield ("usage", self.usage)
        yield ("stop_reason", self.stop_reason or StopReason.END_TURN)

    def _resolve_call_id(self, event: dict) -> str | None:
        call_id = event.get("call_id")
        if call_id:
            return call_id
        item_id = event.get("item_id")
        if item_id:
            return self.item_to_call.get(item_id, item_id)
        return None

    def _try_emit(self, call_id: str, entry: dict) -> Iterable[tuple]:
        if entry["emitted"]:
            return
        entry["emitted"] = True
        # Parse arguments through the shared helper. If the JSON is malformed the
        # helper returns {} and logs; we still emit so the runtime's tool-input
        # validation surfaces a visible error instead of the model silently
        # seeing "turn ended, no tool ran".
        parsed = safe_load_tool_input(entry["arguments"], tool_run_id=call_id)
        # Missing name: the model produced a function_call item with no tool
        # name at all. Emit with a sentinel so execute_tool returns
        # "Unknown tool: <unknown>" and the model sees the failure rather than
        # retrying the same malformed call.
        name = entry["name"] or "<unknown>"
        if name == "<unknown>":
            logger.warning("Tool call %s finalized without name; emitting as <unknown>", call_id)
        yield ("tool_use", ToolUseEvent(id=call_id, name=name, input=parsed))
