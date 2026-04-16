"""Unit tests for the Codex provider (schema strictifier, message/tool conversion,
SSE parser, error translation)."""

from __future__ import annotations

import json

import httpx
import pytest

from agent.providers.base import LLMError, Message, StopReason, ToolDefinition, ToolUseEvent, Usage
from agent.providers.codex_provider import (
    CODEX_RESPONSES_URL,
    CodexClient,
    _convert_messages,
    _convert_tools,
    _parse_codex_stream,
    _strictify_schema,
    iter_sse_events,
)
from agent.oauth.codex_auth import CodexAuth
from agent.oauth.token_store import TokenRecord, TokenStore
from agent.tools import TOOL_DEFINITIONS


# ---------- strict schema ----------

class TestStrictifySchema:
    def test_required_property_kept_as_is(self):
        schema = {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        }
        out = _strictify_schema(schema)
        assert out["additionalProperties"] is False
        assert out["required"] == ["sql"]
        assert out["properties"]["sql"] == {"type": "string"}

    def test_optional_property_made_nullable_and_required(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["name"],
        }
        out = _strictify_schema(schema)
        assert set(out["required"]) == {"name", "limit"}
        assert out["properties"]["name"] == {"type": "string"}  # required, unchanged
        assert out["properties"]["limit"]["type"] == ["integer", "null"]

    def test_all_five_nfl_tools_pass_strict_rules(self):
        for tool in TOOL_DEFINITIONS:
            out = _strictify_schema(tool["input_schema"])
            self._assert_strict(out, trail=tool["name"])

    def test_nested_object_gets_additional_properties_false(self):
        schema = {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "properties": {"year": {"type": "integer"}},
                    "required": [],
                },
            },
            "required": ["filters"],  # keep required so we don't wrap in nullable
        }
        out = _strictify_schema(schema)
        self._assert_strict(out)
        filters = out["properties"]["filters"]
        assert filters["additionalProperties"] is False
        assert filters["type"] == "object"
        # The inner optional `year` was promoted to required AND made nullable.
        assert filters["required"] == ["year"]
        assert filters["properties"]["year"]["type"] == ["integer", "null"]

    def test_optional_nested_object_is_nullable(self):
        schema = {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "properties": {"year": {"type": "integer"}},
                    "required": ["year"],
                },
            },
            "required": [],  # filters is optional
        }
        out = _strictify_schema(schema)
        filters = out["properties"]["filters"]
        # Simple object type → becomes the multi-type form rather than anyOf.
        assert filters["type"] == ["object", "null"]
        assert filters["additionalProperties"] is False

    def test_array_items_are_recursed(self):
        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                        "required": ["id"],
                    },
                }
            },
            "required": ["items"],
        }
        out = _strictify_schema(schema)
        inner = out["properties"]["items"]["items"]
        assert inner["additionalProperties"] is False
        assert inner["required"] == ["id"]

    def test_anyof_variants_are_recursed(self):
        schema = {
            "anyOf": [
                {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
                {"type": "string"},
            ],
        }
        out = _strictify_schema(schema)
        assert out["anyOf"][0]["additionalProperties"] is False
        assert out["anyOf"][0]["required"] == ["a"]

    def test_empty_schema_returns_empty_object(self):
        out = _strictify_schema({})
        assert out == {"type": "object", "additionalProperties": False, "properties": {}, "required": []}

    def test_original_schema_not_mutated(self):
        original = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": [],
        }
        snapshot = json.dumps(original, sort_keys=True)
        _strictify_schema(original)
        assert json.dumps(original, sort_keys=True) == snapshot

    # ---- helper ----
    def _assert_strict(self, node: dict, trail: str = "root"):
        if node.get("type") == "object" or "properties" in node:
            assert node.get("additionalProperties") is False, trail
            props = node.get("properties") or {}
            required = set(node.get("required") or [])
            assert required == set(props.keys()), f"{trail}: required must equal property names"
            for name, child in props.items():
                # Either it has simple required type, or it's nullable. Either is OK for the
                # schema to be accepted; we just recurse.
                if isinstance(child, dict):
                    self._assert_strict(child, trail=f"{trail}.{name}")
        for combiner in ("anyOf", "oneOf", "allOf"):
            for i, variant in enumerate(node.get(combiner) or []):
                if isinstance(variant, dict):
                    self._assert_strict(variant, trail=f"{trail}[{combiner}/{i}]")
        if node.get("type") == "array" and isinstance(node.get("items"), dict):
            self._assert_strict(node["items"], trail=f"{trail}[]")


# ---------- message conversion ----------

class TestConvertMessages:
    def test_user_message(self):
        out = _convert_messages([Message(role="user", text="Hello")])
        assert out == [{
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Hello"}],
        }]

    def test_assistant_text_and_tool_calls_are_separate_items(self):
        out = _convert_messages([
            Message(
                role="assistant",
                text="Let me check.",
                tool_calls=[ToolUseEvent(id="call_1", name="search_players", input={"name": "Mahomes"})],
            )
        ])
        assert len(out) == 2
        assert out[0]["content"][0] == {"type": "output_text", "text": "Let me check."}
        assert out[1] == {
            "type": "function_call",
            "call_id": "call_1",
            "name": "search_players",
            "arguments": '{"name":"Mahomes"}',
        }

    def test_assistant_with_only_tool_calls_emits_only_function_call(self):
        out = _convert_messages([
            Message(
                role="assistant",
                text=None,
                tool_calls=[ToolUseEvent(id="c", name="n", input={})],
            )
        ])
        assert len(out) == 1
        assert out[0]["type"] == "function_call"

    def test_tool_result(self):
        out = _convert_messages([
            Message(role="tool_result", tool_use_id="call_1", tool_content='{"ok":true}')
        ])
        assert out == [{
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"ok":true}',
        }]


# ---------- tool conversion ----------

class TestConvertTools:
    def test_tool_definitions_get_strict_wrapper(self):
        tools = [ToolDefinition(name="t", description="d", input_schema={"type": "object", "properties": {}, "required": []})]
        out = _convert_tools(tools)
        assert out[0]["type"] == "function"
        assert out[0]["name"] == "t"
        assert out[0]["strict"] is True
        assert out[0]["parameters"]["additionalProperties"] is False

    def test_none_tools_returns_empty(self):
        assert _convert_tools(None) == []
        assert _convert_tools([]) == []


# ---------- SSE parser ----------

def _encode_sse(events: list[dict], *, append_done: bool = True) -> list[str]:
    lines: list[str] = []
    for event in events:
        lines.append("data: " + json.dumps(event))
        lines.append("")
    if append_done:
        lines.append("data: [DONE]")
        lines.append("")
    return lines


class TestIterSseEvents:
    def test_parses_single_event(self):
        lines = _encode_sse([{"type": "response.output_text.delta", "delta": "hi"}])
        events = list(iter_sse_events(lines))
        assert events == [{"type": "response.output_text.delta", "delta": "hi"}]

    def test_stops_at_done(self):
        lines = [
            "data: [DONE]",
            "",
            'data: {"type": "ignored"}',
            "",
        ]
        assert list(iter_sse_events(lines)) == []

    def test_ignores_comments_and_unknown_prefixes(self):
        lines = [
            ": heartbeat",
            "event: response.output_text.delta",
            'data: {"type": "response.output_text.delta", "delta": "x"}',
            "",
            "data: [DONE]",
            "",
        ]
        assert list(iter_sse_events(lines)) == [
            {"type": "response.output_text.delta", "delta": "x"},
        ]

    def test_malformed_json_is_dropped(self):
        lines = ["data: {not json", "", "data: [DONE]", ""]
        assert list(iter_sse_events(lines)) == []


# ---------- stream state machine ----------

async def _alines(lines):
    for line in lines:
        yield line


@pytest.mark.asyncio
class TestParseCodexStream:
    async def test_text_deltas_yield_tagged_text(self):
        lines = _encode_sse([
            {"type": "response.output_text.delta", "delta": "Hel"},
            {"type": "response.output_text.delta", "delta": "lo"},
            {"type": "response.done", "response": {"usage": {"input_tokens": 5, "output_tokens": 2}, "output": []}},
        ])
        collected = [item async for item in _parse_codex_stream(_alines(lines))]
        texts = [v for (k, v) in collected if k == "text"]
        assert texts == ["Hel", "lo"]
        usage = next(v for (k, v) in collected if k == "usage")
        assert usage.input_tokens == 5 and usage.output_tokens == 2
        stop = next(v for (k, v) in collected if k == "stop_reason")
        assert stop == StopReason.END_TURN

    async def test_tool_call_assembly_via_delta_done(self):
        lines = _encode_sse([
            {
                "type": "response.output_item.added",
                "item": {"type": "function_call", "id": "item_1", "call_id": "call_abc", "name": "search_players"},
            },
            {"type": "response.function_call_arguments.delta", "item_id": "item_1", "delta": '{"na'},
            {"type": "response.function_call_arguments.delta", "item_id": "item_1", "delta": 'me":"Mahomes"}'},
            {"type": "response.function_call_arguments.done", "call_id": "call_abc", "arguments": '{"name":"Mahomes"}'},
            {"type": "response.done", "response": {"output": []}},
        ])
        collected = [item async for item in _parse_codex_stream(_alines(lines))]
        tool_events = [v for (k, v) in collected if k == "tool_use"]
        assert len(tool_events) == 1
        assert tool_events[0].id == "call_abc"
        assert tool_events[0].name == "search_players"
        assert tool_events[0].input == {"name": "Mahomes"}
        stop = next(v for (k, v) in collected if k == "stop_reason")
        assert stop == StopReason.TOOL_USE

    async def test_tool_call_falls_back_to_response_output(self):
        """If the server sent no delta/done events but included the call in response.output."""
        lines = _encode_sse([
            {
                "type": "response.done",
                "response": {
                    "output": [
                        {"type": "function_call", "call_id": "c1", "name": "get_schema", "arguments": '{"table_name":"players"}'}
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 3},
                },
            }
        ])
        collected = [item async for item in _parse_codex_stream(_alines(lines))]
        tool_events = [v for (k, v) in collected if k == "tool_use"]
        assert tool_events[0].name == "get_schema"
        assert tool_events[0].input == {"table_name": "players"}

    async def test_item_id_resolves_to_call_id_when_call_id_absent_on_delta(self):
        lines = _encode_sse([
            {"type": "response.output_item.added",
             "item": {"type": "function_call", "id": "item_42", "call_id": "call_xyz", "name": "execute_sql"}},
            {"type": "response.function_call_arguments.delta", "item_id": "item_42", "delta": '{"sql":"SELECT 1"}'},
            {"type": "response.function_call_arguments.done", "item_id": "item_42"},
            {"type": "response.done", "response": {"output": []}},
        ])
        collected = [item async for item in _parse_codex_stream(_alines(lines))]
        tool_events = [v for (k, v) in collected if k == "tool_use"]
        assert tool_events[0].id == "call_xyz"
        assert tool_events[0].input == {"sql": "SELECT 1"}

    async def test_error_event_raises_llmerror(self):
        lines = _encode_sse([
            {"type": "error", "error": {"message": "model overloaded"}},
        ])
        with pytest.raises(LLMError, match="model overloaded"):
            [item async for item in _parse_codex_stream(_alines(lines))]

    async def test_malformed_tool_arguments_emit_empty_input(self):
        # Malformed JSON args should still surface a tool_use with input={}
        # so the runtime's tool validation flags the failure (required fields
        # missing) rather than leaving the turn looking like a clean end_turn.
        lines = _encode_sse([
            {"type": "response.output_item.added",
             "item": {"type": "function_call", "id": "i1", "call_id": "c1", "name": "search_players"}},
            {"type": "response.function_call_arguments.done", "call_id": "c1", "arguments": "{not json"},
            {"type": "response.done", "response": {"output": []}},
        ])
        collected = [item async for item in _parse_codex_stream(_alines(lines))]
        tool_events = [v for (k, v) in collected if k == "tool_use"]
        assert len(tool_events) == 1
        assert tool_events[0].id == "c1"
        assert tool_events[0].name == "search_players"
        assert tool_events[0].input == {}

    async def test_tool_call_only_emitted_once_even_if_done_repeated(self):
        lines = _encode_sse([
            {"type": "response.output_item.added",
             "item": {"type": "function_call", "id": "i1", "call_id": "c1", "name": "n"}},
            {"type": "response.function_call_arguments.done", "call_id": "c1", "arguments": "{}"},
            {"type": "response.output_item.done",
             "item": {"type": "function_call", "id": "i1", "call_id": "c1", "name": "n", "arguments": "{}"}},
            {"type": "response.done", "response": {"output": [
                {"type": "function_call", "id": "i1", "call_id": "c1", "name": "n", "arguments": "{}"}
            ]}},
        ])
        collected = [item async for item in _parse_codex_stream(_alines(lines))]
        assert len([v for (k, v) in collected if k == "tool_use"]) == 1


# ---------- CodexClient (end-to-end with mocked transport) ----------

def _fake_jwt(payload: dict) -> str:
    import base64
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.sig"


def _make_authed_client(tmp_path, sse_body: bytes, status: int = 200) -> CodexClient:
    import time
    store = TokenStore(tmp_path / "codex_auth.json")
    access_token = _fake_jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct_z"}})
    record = TokenRecord(
        access_token=access_token,
        refresh_token="rt",
        expires_at_ms=int(time.time() * 1000) + 3_600_000,
        id_token=_fake_jwt({"email": "u@x.com"}),
        email="u@x.com",
    )
    store.save(record)

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            status,
            headers={"Content-Type": "text/event-stream"},
            content=sse_body,
        )

    transport = httpx.MockTransport(handler)
    auth_http = httpx.AsyncClient(transport=transport)
    codex_http = httpx.AsyncClient(transport=transport)
    auth = CodexAuth(store, http_client=auth_http)
    client = CodexClient(model="gpt-5.1-codex", auth=auth, http_client=codex_http)
    client._captured = captured  # type: ignore[attr-defined]  (for test inspection)
    return client


def _sse_body(events: list[dict]) -> bytes:
    return ("\n".join(_encode_sse(events)) + "\n").encode("utf-8")


@pytest.mark.asyncio
class TestCodexClientEndToEnd:
    async def test_create_message_aggregates_text_and_usage(self, tmp_path):
        sse = _sse_body([
            {"type": "response.output_text.delta", "delta": "Ans"},
            {"type": "response.output_text.delta", "delta": "wer"},
            {"type": "response.done", "response": {"usage": {"input_tokens": 7, "output_tokens": 3}, "output": []}},
        ])
        client = _make_authed_client(tmp_path, sse)
        try:
            resp = await client.create_message([Message(role="user", text="Hi")])
            assert len(resp.content) == 1
            assert resp.content[0].text == "Answer"
            assert resp.stop_reason == StopReason.END_TURN
            assert resp.usage.input_tokens == 7
            assert resp.usage.output_tokens == 3
            assert client.last_usage.input_tokens == 7
            # Request headers sanity check
            req = client._captured["request"]  # type: ignore[attr-defined]
            assert req.headers["chatgpt-account-id"] == "acct_z"
            assert req.headers["OpenAI-Beta"] == "responses=experimental"
            assert str(req.url) == CODEX_RESPONSES_URL
            body = json.loads(req.content)
            assert body["model"] == "gpt-5.1-codex"
            assert body["store"] is False
            assert body["stream"] is True
        finally:
            await client.aclose()

    async def test_stream_message_yields_text_and_tool_events(self, tmp_path):
        sse = _sse_body([
            {"type": "response.output_text.delta", "delta": "Looking up. "},
            {"type": "response.output_item.added",
             "item": {"type": "function_call", "id": "i1", "call_id": "c1", "name": "search_players"}},
            {"type": "response.function_call_arguments.done", "call_id": "c1", "arguments": '{"name":"Mahomes"}'},
            {"type": "response.done", "response": {"output": [], "usage": {"input_tokens": 1, "output_tokens": 1}}},
        ])
        client = _make_authed_client(tmp_path, sse)
        try:
            events = []
            async for event in client.stream_message([Message(role="user", text="Q")]):
                events.append(event)
            from agent.providers.base import TextEvent as _TextEvent
            texts = [e.text for e in events if isinstance(e, _TextEvent)]
            tools = [e for e in events if isinstance(e, ToolUseEvent)]
            assert texts == ["Looking up. "]
            assert len(tools) == 1
            assert tools[0].name == "search_players"
            assert tools[0].input == {"name": "Mahomes"}
            assert client.last_usage.input_tokens == 1
        finally:
            await client.aclose()

    async def test_http_error_is_translated(self, tmp_path):
        body = json.dumps({"error": {"message": "bad request"}}).encode()
        client = _make_authed_client(tmp_path, body, status=400)
        try:
            with pytest.raises(LLMError, match="bad request"):
                await client.create_message([Message(role="user", text="x")])
        finally:
            await client.aclose()

    async def test_401_translates_to_auth_message(self, tmp_path):
        client = _make_authed_client(tmp_path, b'{"error": {"message": "token expired"}}', status=401)
        try:
            with pytest.raises(LLMError, match="re-authenticate"):
                await client.create_message([Message(role="user", text="x")])
        finally:
            await client.aclose()
