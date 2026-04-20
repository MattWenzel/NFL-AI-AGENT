# Providers

One codebase, two LLM SDKs. The provider layer is a thin adapter that translates a **canonical message format** into each SDK's wire protocol and normalizes their events back out. Everything above the adapter (runtime, tools, compaction) is provider-agnostic; everything below is provider-specific.

## File map

- `provider/base.py` — `BaseLLMClient` ABC, canonical types.
- `provider/anthropic.py` — `AnthropicClient`.
- `provider/openai.py` — `OpenAIClient`.
- `provider/__init__.py` — registry, factory, default provider definitions.
- `server/dependencies.py` — per-request client construction.

## Canonical types

Defined at `base.py`. Every provider speaks these in and out.

### `Message` (`base.py:75`)

Three roles:

| Role | Fields set | Shape |
|------|-----------|-------|
| `"user"` | `text` | User message text. |
| `"assistant"` | `text`, `tool_calls` | Assistant response; may carry both text and tool calls. |
| `"tool_result"` | `tool_use_id`, `tool_content` | Output of one tool call, keyed by the call's id. |

There is no separate `"system"` role — system prompts are passed as a `system=` kwarg on `create_message` / `stream_message`, not as a message.

### `ToolUseEvent`, `TextEvent` (`base.py:53-65`)

What streams look like: a sequence of `TextEvent(text)` and `ToolUseEvent(id, name, input)`. The runtime's `run_session` dispatches on `isinstance(event, TextEvent | ToolUseEvent)` with no knowledge of which provider produced them.

### `MessageResponse` (`base.py:67`)

Non-streaming response shape: `content: list[TextEvent | ToolUseEvent]`, `stop_reason: StopReason`, `usage: Usage`. Used by the compaction summarizer (which doesn't need streaming).

### `StopReason` (`base.py:14`)

Enum with three values: `END_TURN`, `TOOL_USE`, `MAX_TOKENS`. Each provider maps its native strings:

| Canonical | Anthropic | OpenAI |
|-----------|-----------|--------|
| `END_TURN` | `"end_turn"` | `"stop"` |
| `TOOL_USE` | `"tool_use"` | `"tool_calls"` |
| `MAX_TOKENS` | `"max_tokens"` | `"length"` |

The runtime's MAX_TOKENS guard (see [runtime.md](runtime.md#runtimelooperror)) reads the normalized value — no provider check needed.

### `ToolDefinition` (`base.py:28`)

Anthropic-format by convention: `name`, `description`, `input_schema` (JSON Schema). `to_dict()` / `from_dict()` round-trip Anthropic's wire shape. The OpenAI adapter re-wraps these into OpenAI's `{"type": "function", "function": {...}}` envelope at the boundary.

## `BaseLLMClient` ABC

`base.py:90`. Six methods:

| Method | Purpose |
|--------|---------|
| `create_message(messages, tools, system, model) -> MessageResponse` | Non-streaming. Used by compaction summarizer. |
| `stream_message(messages, tools, system) -> AsyncIterator` | Streaming. Used by the main runtime loop. |
| `provider_name` property | `"anthropic"` / `"openai"`. |
| `_translate_error(exc)` | Map SDK exceptions to `LLMError`. |
| `aclose()` | Release SDK-owned resources (default no-op). |
| `_wrap_api_errors()` | Context manager that funnels uncaught exceptions through `_translate_error`. |

Two state fields are shared by both clients (`base.py:96`):

- `last_usage: Usage` — tokens for the most recent call. Read by the runtime to write `input_tokens` / `output_tokens` on the assistant turn.
- `last_stop_reason: StopReason | None` — last call's stop reason. Read by the runtime to detect `MAX_TOKENS` without a tool call.

Both are reset by the runtime at the top of each iteration (`runtime.py:159`) so a previous turn's stats don't leak forward if the current call never emits usage.

### `model` override on `create_message`

`base.py:111`. A single-call override used by the compaction summarizer to fire a cheap sibling model without swapping the long-lived client. `create_message(model="claude-haiku-4-5-20251001")` from a Sonnet client works. `stream_message` has no override — main-loop calls always use the session's selected model.

## Provider registry

`__init__.py:15`. `ProviderInfo` is the registration record:

```python
@dataclass
class ProviderInfo:
    name: str              # "anthropic"
    display_name: str      # "Anthropic"
    env_key: str           # "ANTHROPIC_API_KEY"
    default_model: str
    summarizer_model: str | None
    models: list[str]
    context_window: int
    max_output_tokens: int
    supports_streaming: bool
    supports_tools: bool
    client_class: type[BaseLLMClient]
```

The `effective_context_window` property (`__init__.py:34`) returns 75% of `context_window` — this is what seeds a new session's compaction threshold. The reserved 25% accommodates the next assistant response plus per-call overhead. See [compaction.md](compaction.md#the-trigger).

### Registered providers

| Provider | Default model | Context | Max output | Summarizer |
|----------|---------------|---------|------------|------------|
| `anthropic` | `claude-sonnet-4-6` | 200K | 64K | `claude-haiku-4-5-20251001` |
| `openai` | `gpt-5` | 128K | 16K | `gpt-5-mini` |

Anthropic is registered unconditionally at import (`__init__.py:99`). OpenAI is wrapped in `try/except ImportError` (`__init__.py:121`) — if the optional `openai` SDK isn't installed, the provider simply doesn't appear in the registry. The UI's provider dropdown is driven by `list_providers`, so dropping the SDK dependency cleanly removes the option.

### Summarizer model

Compaction runs at most once every ~N turns of a long conversation, but if it used the main model (Sonnet / gpt-5) each call would burn 60K input tokens at main-model rates. The `summarizer_model` override routes that one call through Haiku / gpt-5-mini instead — same provider (same API key, same SDK), cheaper model.

Declared per-provider so each provider controls its own sibling choice. Looked up by the compaction layer via `get_provider(name).summarizer_model`. Falls back to the session's active model if `None`.

## Per-request client construction

`server/dependencies.py:97`. Clients are not cached. Every chat request builds a fresh one:

```
create_client_for_request(provider, model, api_key)
    ↓
create_client(provider, model, api_key)     # provider/__init__.py:67
    ├─ resolve provider name (arg > env CHAT_PROVIDER > "anthropic")
    ├─ resolve model (arg > provider default)
    ├─ resolve key (arg > env)
    └─ instantiate client_class(model=..., api_key=..., max_output_tokens=...)
```

Why no caching: the API key varies per user. The transport layer looks up the authenticated user's stored key (Fernet-decrypted from `user_api_keys`) and passes it to `create_client_for_request`. Multi-user setups can't reuse a client across users without risking cross-user leakage. See [auth.md](auth.md#api-keys) for key storage.

Callers own cleanup (`dependencies.py:130`): a `try/finally` around the request wraps `await close_client(client)`, which calls `aclose()`. On the Anthropic SDK, this releases the underlying httpx session; on OpenAI the default no-op is fine.

## Anthropic adapter

`anthropic.py`.

### Message translation

`_convert_messages` (`anthropic.py:146`). Maps canonical `Message` to Anthropic's wire shape:

- `user` → `{"role": "user", "content": text}`.
- `assistant` → `{"role": "assistant", "content": [blocks]}` where blocks are `{"type": "text", ...}` and `{"type": "tool_use", "id", "name", "input"}`.
- `tool_result` → `{"role": "user", "content": [{"type": "tool_result", "tool_use_id", "content"}]}`. Anthropic encodes tool results as user-role messages.

After conversion, **consecutive user-role tool-result messages are merged** (`anthropic.py:179`) into a single message with multiple tool_result blocks. Anthropic requires strictly alternating user/assistant roles, and the runtime often emits multiple tool results from one pass as separate Messages.

### Streaming

`stream_message` (`anthropic.py:73`). The Anthropic SDK yields discrete event types; we dispatch:

| Event | Action |
|-------|--------|
| `content_block_start` with `tool_use` block | Capture `id` and `name` into `current_tool_*` accumulators. |
| `content_block_delta` with `text_delta` | Yield `TextEvent(text)`. |
| `content_block_delta` with `input_json_delta` | Append partial JSON to accumulator. |
| `content_block_stop` | If we were accumulating a tool call, JSON-decode and yield `ToolUseEvent`. |
| `message_start` | Grab `input_tokens` from usage. |
| `message_delta` | Grab `output_tokens` + stop reason. |

Tool calls are **atomic** in Anthropic streams: each one starts, streams partial JSON, and stops before the next begins. That simplifies accumulation — one active call at a time.

JSON decode failures log a warning and yield an empty dict `{}`. The runtime still persists the tool call; validation at dispatch time catches it and returns an error envelope.

## OpenAI adapter

`openai.py`.

### Message translation

`_convert_messages` (`openai.py:180`). Maps canonical `Message` to OpenAI's wire shape:

- `user` → `{"role": "user", "content": text}`.
- `assistant` → `{"role": "assistant", "content": text, "tool_calls": [{"id", "type": "function", "function": {"name", "arguments": json_str}}]}`. OpenAI flattens tool calls onto the assistant message; arguments are a JSON *string*, not a dict.
- `tool_result` → `{"role": "tool", "tool_call_id", "content"}`. OpenAI has a dedicated `tool` role (Anthropic reuses `user`).

System prompt placement (`openai.py:239`): OpenAI wants system as a message in the list; we prepend `{"role": "system", "content": system}` in `_build_kwargs`. Anthropic wants it as a top-level `system=` kwarg; no message is emitted.

### Tool schema translation

`_convert_tools` (`openai.py:217`). Wraps each `ToolDefinition` into OpenAI's function shape:

```python
{"type": "function", "function": {"name", "description", "parameters": input_schema}}
```

The inner `parameters` is the same JSON Schema Anthropic uses. The wrapping is the only difference; no field renames or type coercion.

### Streaming

`stream_message` (`openai.py:76`). OpenAI's streaming model is fundamentally different from Anthropic's:

- **Text arrives as `delta.content`** — same as Anthropic, straightforward.
- **Tool calls are fragmented by index.** Each chunk carries `delta.tool_calls[]`, but the elements identify themselves by `.index` — multiple tool calls interleave in the stream, and each call's `id`, `name`, and `arguments` (a JSON string) arrive in pieces across chunks.

The accumulator `tool_calls_acc: dict[int, dict]` (`openai.py:94`) keys by `index` and accumulates `id`, `name`, `arguments` strings. When `finish_reason == "tool_calls"` arrives, `_emit_accumulated_tools` (`openai.py:161`) JSON-decodes each accumulator entry and yields `ToolUseEvent`s.

Edge case: if the stream ends with accumulated tool calls but no `finish_reason == "tool_calls"` (e.g., length truncation mid-tool-call), the post-loop block at `openai.py:145` emits whatever accumulated. A warning is logged so this can be investigated.

Usage is emitted in a trailing chunk when `stream_options={"include_usage": True}` (`openai.py:88`). Pulled from `chunk.usage.prompt_tokens` / `completion_tokens`.

## Error translation

Each client overrides `_translate_error` (`anthropic.py:38`, `openai.py:39`) to map SDK exceptions into `LLMError`:

- `AuthenticationError` → "Invalid X API key — update it in Settings"
- `RateLimitError` → "Rate limited by X — retry shortly"
- Other `APIError` → passes the SDK message through
- Fallback → `f"X error: {exc}"`

The `_wrap_api_errors` context manager on `BaseLLMClient` (`base.py:147`) wraps every call site so SDK exceptions are converted once and not repeated. The streaming path does its own try/except because the context manager exits before the stream is consumed.

## Adding a provider

1. Write `provider/myprovider.py` with a `BaseLLMClient` subclass implementing `create_message`, `stream_message`, `provider_name`, and `_translate_error`.
2. Register it in `__init__.py` — add a `ProviderInfo` with `client_class=MyProviderClient` and set `summarizer_model` to a cheap sibling. Wrap the registration in `try/except ImportError` if the SDK is optional.
3. Map the SDK's stop-reason strings to `StopReason` in a `_STOP_MAP` constant.
4. Translate `Message` both ways in `_convert_messages`; if tool result role differs from Anthropic/OpenAI, match the provider's convention there.
5. Expose the canonical types in `__init__.py`'s `__all__` if new types are introduced.

No changes to `runtime/`, `tools/`, or `api/` — the adapter is the seam.
