# Providers

One codebase, three LLM providers — Anthropic Claude, OpenAI (Chat Completions), and OpenAI Codex (ChatGPT OAuth via the internal Responses API). The provider layer is a thin adapter that translates a **canonical message format** into each SDK's wire protocol and normalizes their events back out. Everything above the adapter (runtime, tools, compaction) is provider-agnostic; everything below is provider-specific.

## File map

- `backend/domain/providers/base.py` — `BaseLLMClient` ABC + canonical types (`Message`, `TextEvent`, `ToolUseEvent`, `RetryingEvent`, `Usage`, `ToolDefinition`, `ToolChoice`, `StopReason`, `LLMError`, `ContextOverflowError`).
- `backend/domain/providers/__init__.py` — `ProviderInfo` registry, `create_client` factory, built-in provider registration.
- `backend/domain/providers/clients/anthropic.py` — `AnthropicClient` (official SDK, ephemeral-cache prompt caching).
- `backend/domain/providers/clients/openai.py` — `OpenAIClient` (official SDK, Chat Completions).
- `backend/domain/providers/clients/codex.py` — `OpenAICodexClient` (OAuth access token; hits OpenAI's internal Responses API via raw `httpx` + SSE parsing).
- `backend/domain/providers/retry.py` — header-aware exponential backoff (`parse_retry_after`, `compute_delay`, `with_retries`, `RetryableError`).
- `backend/domain/providers/overflow.py` — `is_context_overflow(msg)` regex match for "too long" errors; maps provider-specific error strings to the canonical `ContextOverflowError`.
- `backend/domain/providers/tool_calls.py` — shared helpers for assembling streamed tool-call fragments.

## Canonical types

Defined in `base.py`. Every provider speaks these in and out.

### `Message` (`base.py:121`)

Three roles:

| Role | Fields set | Shape |
|------|-----------|-------|
| `"user"` | `text` | User message text. |
| `"assistant"` | `text`, `tool_calls` | Assistant response; may carry both text and tool calls. |
| `"tool_result"` | `tool_use_id`, `tool_content` | Output of one tool call, keyed by the call's id. |

There is no separate `"system"` role — system prompts are passed as a `system=` kwarg on `stream_message` / `create_message`, not as a message.

### `TextEvent`, `ToolUseEvent`, `RetryingEvent` (`base.py:84-110`)

What streams look like: a sequence of `TextEvent(text)` and `ToolUseEvent(id, name, input)`, plus occasional `RetryingEvent(attempt, delay_seconds, error_message)` when the adapter has to retry an API call before any content flows (see [Retry behavior](#retry-behavior)). The runtime's `run_session` dispatches on `isinstance(event, …)` with no knowledge of which provider produced them.

### `MessageResponse` (`base.py:113`)

Non-streaming response shape: `content: list[TextEvent | ToolUseEvent]`, `stop_reason: StopReason`, `usage: Usage`. Used by the compaction summarizer (which doesn't need streaming).

### `StopReason` (`base.py:37`)

Enum with three values: `END_TURN`, `TOOL_USE`, `MAX_TOKENS`. Each provider maps its native strings:

| Canonical | Anthropic | OpenAI | Codex |
|-----------|-----------|--------|-------|
| `END_TURN` | `"end_turn"` | `"stop"` | `"stop"` (`response.done`) |
| `TOOL_USE` | `"tool_use"` | `"tool_calls"` | emits `function_call` item |
| `MAX_TOKENS` | `"max_tokens"` | `"length"` | `"max_output_tokens"` |

The runtime's MAX_TOKENS guard (see [runtime.md](runtime.md#runtimelooperror)) reads the normalized value — no provider check needed.

### `Usage` (`base.py:44`)

Fields: `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`. Cache fields are only populated by the Anthropic adapter; OpenAI / Codex leave them at 0.

### `ToolDefinition` (`base.py:59`)

Anthropic-format by convention: `name`, `description`, `input_schema` (JSON Schema). `to_dict()` / `from_dict()` round-trip Anthropic's wire shape. The OpenAI adapter re-wraps these into OpenAI's `{"type": "function", "function": {...}}` envelope; Codex re-wraps into Responses-API function-tool shape with strict-mode schema rewriting.

### `ToolChoice` (`base.py:17`) and `CredentialShape` (`base.py:10`)

- `ToolChoice`: literal `"auto" | "required" | "none"` — how aggressively the model should pick a tool. Each adapter translates to its native value.
- `CredentialShape`: literal `"api_key" | "codex_oauth"` — tells the transport/credential layer how to source the secret. Anthropic + OpenAI use `api_key`; Codex uses `codex_oauth` (device-code flow).

## `BaseLLMClient` ABC

`base.py:135`. Six methods every adapter implements:

| Method | Purpose |
|--------|---------|
| `create_message(messages, tools, system, model=None)` | Non-streaming. Used by the compaction summarizer. `model=` overrides the client's model for one call. |
| `stream_message(messages, tools, system, tool_choice=None)` | Streaming. Used by the main runtime loop. |
| `provider_name` property | `"anthropic"` / `"openai"` / `"openai-codex"`. |
| `_translate_error(exc)` | Map SDK exceptions to `LLMError` (or `ContextOverflowError` when the error body matches `is_context_overflow`). |
| `aclose()` | Release SDK-owned resources (default no-op; Codex closes its httpx client). |
| `_wrap_api_errors()` | Context manager that funnels uncaught exceptions through `_translate_error`. |

Two state fields are shared by all clients (`base.py:141`):

- `last_usage: Usage` — tokens for the most recent call. Read by the runtime to write `input_tokens` / `output_tokens` on the assistant turn.
- `last_stop_reason: StopReason | None` — last call's stop reason. Read by the runtime to detect `MAX_TOKENS` without a tool call.

Both are reset by the runtime at the top of each iteration (`runtime.py:128-129`) so a previous turn's stats don't leak forward if the current call never emits usage.

### `model` override on `create_message`

A single-call override used by the compaction summarizer to fire a cheap sibling model without swapping the long-lived client. `create_message(model="claude-haiku-4-5-20251001")` from a Sonnet client works. `stream_message` has no override — main-loop calls always use the session's selected model.

## Provider registry

`__init__.py:16`. `ProviderInfo` is the registration record:

```python
@dataclass
class ProviderInfo:
    name: str                 # "anthropic"
    display_name: str         # "Anthropic"
    env_key: str              # "ANTHROPIC_API_KEY"
    default_model: str
    summarizer_model: str | None
    models: list[str]
    context_window: int
    max_output_tokens: int
    supports_streaming: bool
    supports_tools: bool
    client_class: type[BaseLLMClient] | None
    credential_shape: CredentialShape   # "api_key" or "codex_oauth"
```

The `effective_context_window` property (`__init__.py:39`) returns 75% of `context_window` — this is what seeds a new session's compaction threshold. The reserved 25% accommodates the next assistant response plus per-call overhead. See [compaction.md](compaction.md#the-trigger).

### Registered providers

All three are registered at import by `ensure_builtin_providers_registered` (`__init__.py:116`). OpenAI is wrapped in `try/except ImportError` so dropping the `openai` SDK cleanly removes the option from the registry (and therefore from the UI's dropdown).

| Provider | Default model | Context | Max output | Summarizer | Credential |
|----------|---------------|---------|------------|------------|-----------|
| `anthropic` | `claude-sonnet-4-6` | 200K | 64K | `claude-haiku-4-5-20251001` | `api_key` |
| `openai` | `gpt-5` | 128K | 16K | `gpt-5-mini` | `api_key` |
| `openai-codex` | `gpt-5.3-codex` | 200K | 16K | `gpt-5.3-codex` | `codex_oauth` |

### Summarizer model

Compaction runs at most once every ~N turns of a long conversation, but if it used the main model (Sonnet / gpt-5) each call would burn 60K input tokens at main-model rates. The `summarizer_model` override routes that one call through Haiku / gpt-5-mini instead — same provider (same API key/SDK), cheaper model.

Declared per-provider so each provider controls its own sibling choice. Looked up by the compaction layer via `get_provider(name).summarizer_model`. Falls back to the session's active model if `None` — which is what Codex does, since there's no cheap sibling through the OAuth path.

## Per-request client construction

Clients are not cached. Every chat request builds a fresh one via `ChatService.prepare_chat` (see [transport.md](transport.md#services-layer)):

```
ChatService.prepare_chat
    ├─ credentials.get_api_key(user_id, provider_name)   backend/application/oauth/provider_credentials.py
    │     ├─ api_key shape   → decrypt stored key
    │     └─ codex_oauth     → codex_credentials.resolve_access_token (refresh if near expiry)
    │
    └─ create_client(provider, model, api_key=user_key)   backend/application/chat/service.py
          └─ create_client(provider, model, api_key)                  backend/domain/providers/__init__.py:75
                ├─ resolve provider (arg → env CHAT_PROVIDER → "anthropic")
                ├─ resolve model (arg → provider default)
                ├─ resolve key (arg → env[info.env_key])
                └─ instantiate info.client_class(model=…, api_key=…, max_output_tokens=…)
```

Why no caching: the API key varies per user. The transport layer looks up the authenticated user's stored key (Fernet-decrypted from `user_api_keys`, or an OAuth-refreshed access token for Codex) and passes it down. Multi-user setups can't reuse a client across users without risking cross-user leakage. See [auth.md](auth.md#api-keys) for key storage and [transport.md](transport.md#codex-oauth-flow-overview) for the OAuth refresh path.

Callers own cleanup: `close_client` (`backend/application/chat/service.py`) calls `client.aclose()` in a `try/finally`. Anthropic's SDK releases its httpx session; OpenAI's is a no-op; Codex closes its own httpx client.

## Anthropic adapter

`backend/domain/providers/clients/anthropic.py`.

### Message translation

`_convert_messages` (`anthropic.py:269`). Maps canonical `Message` to Anthropic's wire shape:

- `user` → `{"role": "user", "content": text}`.
- `assistant` → `{"role": "assistant", "content": [blocks]}` where blocks are `{"type": "text", ...}` and `{"type": "tool_use", "id", "name", "input"}`.
- `tool_result` → `{"role": "user", "content": [{"type": "tool_result", "tool_use_id", "content"}]}`. Anthropic encodes tool results as user-role messages.

After conversion, **consecutive user-role tool-result messages are merged** into a single message with multiple tool_result blocks. Anthropic requires strictly alternating user/assistant roles, and the runtime often emits multiple tool results from one pass as separate Messages.

### Prompt caching

`_build_kwargs` (`anthropic.py:317-377`):

- The system prompt is wrapped in `[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]`, so Claude caches it across turns.
- A cache breakpoint is added to the last tool_result block of the latest message, extending the cache window to include the full transcript through the previous tool output. Subsequent turns re-use this cached prefix.

`_extract_cache_usage` (`anthropic.py:380`) pulls `cache_read_input_tokens` and `cache_creation_input_tokens` from the response and records them in `Usage` so the logs show cache hit rate.

### Streaming

`stream_message` (`anthropic.py:148`). The Anthropic SDK yields discrete event types; the adapter dispatches on them:

| Event | Action |
|-------|--------|
| `content_block_start` with `tool_use` block | Capture `id`, `name`. |
| `content_block_delta` with `text_delta` | Yield `TextEvent(text)`. |
| `content_block_delta` with `input_json_delta` | Append partial JSON to the tool-input accumulator. |
| `content_block_stop` | If accumulating a tool call, JSON-decode and yield `ToolUseEvent` (via `provider.tool_calls.build_tool_use_event`). |
| `message_start` | Grab `input_tokens` + cache tokens from usage. |
| `message_delta` | Grab `output_tokens` + stop reason. |

Tool calls are **atomic** in Anthropic streams: each one starts, streams partial JSON, and stops before the next begins. That simplifies accumulation — one active call at a time.

JSON decode failures log a warning and yield an empty dict `{}`. The runtime still persists the tool call; validation at dispatch time catches the bad input and returns an error envelope.

## OpenAI adapter

`backend/domain/providers/clients/openai.py`.

### Message translation

`_convert_messages` (`openai.py:274`). Maps canonical `Message` to OpenAI's Chat Completions wire shape:

- `user` → `{"role": "user", "content": text}`.
- `assistant` → `{"role": "assistant", "content": text, "tool_calls": [{"id", "type": "function", "function": {"name", "arguments": json_str}}]}`. OpenAI flattens tool calls onto the assistant message; `arguments` is a JSON *string*, not a dict.
- `tool_result` → `{"role": "tool", "tool_call_id", "content"}`. OpenAI has a dedicated `tool` role (Anthropic reuses `user`).

System prompt placement: OpenAI wants system as a message in the list; the adapter prepends `{"role": "system", "content": system}` in `_build_kwargs` (`openai.py:310`). Anthropic wants it as a top-level `system=` kwarg; no message is emitted.

### Tool schema translation

`_convert_tools` (`openai.py:310`). Wraps each `ToolDefinition` into OpenAI's function shape:

```python
{"type": "function", "function": {"name", "description", "parameters": input_schema}}
```

The inner `parameters` is the same JSON Schema Anthropic uses. The wrapping is the only difference; no field renames or type coercion.

### Streaming

`stream_message` (`openai.py:136`). OpenAI's streaming model is fundamentally different from Anthropic's:

- **Text arrives as `delta.content`** — same as Anthropic, straightforward.
- **Tool calls are fragmented by index.** Each chunk carries `delta.tool_calls[]`, but the elements identify themselves by `.index` — multiple tool calls can interleave in the stream, and each call's `id`, `name`, and `arguments` (a JSON string) arrive in pieces across chunks.

The accumulator `tool_calls_acc: dict[int, dict]` keys by `index` and accumulates `id`, `name`, `arguments` strings. When `finish_reason == "tool_calls"` arrives, `_emit_accumulated_tools` (`openai.py:262`) JSON-decodes each accumulator entry and yields `ToolUseEvent`s.

Edge case: if the stream ends with accumulated tool calls but no `finish_reason == "tool_calls"` (length truncation mid-tool-call, for example), the post-loop block emits whatever accumulated. A warning is logged so it can be investigated.

Usage is emitted in a trailing chunk when `stream_options={"include_usage": True}`. Pulled from `chunk.usage.prompt_tokens` / `completion_tokens`.

## Codex adapter (ChatGPT OAuth)

`backend/domain/providers/clients/codex.py`. The one adapter that doesn't use an SDK — it hits OpenAI's internal Responses API via raw `httpx` + SSE parsing because that's the endpoint the OAuth access token is scoped to.

### Authentication

`OpenAICodexClient.__init__` (`codex.py:80`). Takes the OAuth access token as `api_key`. Refuses to instantiate without one (no env-var fallback — this provider is always user-scoped). Extracts the `account_id` from the JWT (`codex.py:86`) and sends it as a header on every request. If the token's near expiry, `backend/application/oauth/codex/credentials.py` refreshes it under a per-user lock *before* this client is constructed; see [transport.md](transport.md#codex-oauth-flow-overview).

### Message format

Unlike Chat Completions, Responses API uses an `input: list[…]` where **each assistant text + each tool call is a separate item** (`codex.py:405`):

```python
{"type": "message", "role": "user", "content": [{"type": "input_text", "text": ...}]}
{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": ...}]}
{"type": "function_call", "call_id": ..., "name": ..., "arguments": JSON_string}
{"type": "function_call_output", "call_id": ..., "output": ...}
```

### Strict-mode tool schemas

`_make_strict_schema` (`codex.py:446`) recursively rewrites every tool's `input_schema`: every property becomes `required` and every originally-optional property is made nullable (`type: ["string", "null"]`). This is what the Responses API expects. `validation.py::_strip_codex_nulls` compensates on the way in (see [tools.md](tools.md#input-validation)) so handlers don't have to care.

### SSE streaming

Custom `_iter_sse` parser (`codex.py:351`): reads lines, buffers until a blank line, decodes `data: {json}` payloads. Event types:

- `response.output_text.delta` → yield `TextEvent`.
- `response.output_item.added` → record `(item_id → call_id, name)` mapping for tool calls.
- `response.function_call_arguments.delta` → append to the accumulator.
- `response.function_call_arguments.done` / `response.output_item.done` → assemble and yield `ToolUseEvent`.
- `response.done` / `response.completed` → pull usage + stop reason.

Tool assembly resolves `call_id` via the `item_to_call` mapping so out-of-order delta/done events still produce correct events.

## Retry behavior

`backend/domain/providers/retry.py`. All three providers use the same backoff primitives; only the error classifier differs.

**Config** (`retry.py:30-34`):

- `MAX_ATTEMPTS = 4` (initial + 3 retries).
- `BASE_DELAY = 2.0` s, `MAX_DELAY = 30.0` s (no-header cap), `HEADER_MAX_DELAY = 120.0` s.
- `JITTER_FRAC = 0.25` (±25% jitter when computing delay without a Retry-After hint).

**Retry-After parsing**:

- `parse_retry_after(value)` — delta-seconds or HTTP-date.
- `parse_retry_after_ms(value)` — Anthropic's `retry-after-ms` header, which wins over the standard `retry-after` when both are present.

**`with_retries(op, classify, on_retry=...)`** (`retry.py:93`) is the shared wrapper. Each provider supplies its own `classify` function:

- **Anthropic** (`anthropic.py:43`): rate-limit, 5xx, 529, connection/timeout → retryable.
- **OpenAI** (`openai.py:46`): rate-limit, 5xx, connection/timeout → retryable.
- **Codex** (`codex.py:55`): 429, 5xx, connection/timeout → retryable. 401 / 403 propagate immediately so the user gets a "reconnect ChatGPT" message fast.

Providers only retry **stream-open** (before the first event yields). If the stream has already yielded content, a mid-stream error can't be retried transparently — the runtime would see duplicate content. The adapter emits a `RetryingEvent` on each retry so the UI can show progress rather than a silent stall.

## Context-overflow detection

`backend/domain/providers/overflow.py`. One function: `is_context_overflow(message_text: str) -> bool`. Regex list (case-insensitive) matching patterns like `"prompt is too long"`, `"context_length_exceeded"`, `"input token count … exceed"`, `"context (window|length) … exceed"`.

Each provider's `_translate_error` checks `is_context_overflow(body)` and raises `ContextOverflowError` on match instead of `LLMError`. The runtime catches `ContextOverflowError` specifically (see [runtime.md](runtime.md#context-overflow-recovery)) and triggers a one-shot forced compaction retry.

## Error translation

Each client overrides `_translate_error` to map SDK exceptions:

- `AuthenticationError` → `"Invalid X API key — update it in Settings"` (Codex: `"ChatGPT session expired — reconnect in Settings"`).
- `RateLimitError` → `"Rate limited by X — retry shortly"`.
- Overflow-shaped error bodies → `ContextOverflowError`.
- Other `APIError` → passes the SDK message through.
- Fallback → `f"X error: {exc}"`.

The `_wrap_api_errors` context manager on `BaseLLMClient` wraps every call site so SDK exceptions are converted once. The streaming path does its own try/except because the context manager exits before the stream is consumed.

## Adding a provider

1. Write `backend/domain/providers/myprovider.py` with a `BaseLLMClient` subclass implementing `create_message`, `stream_message`, `provider_name`, and `_translate_error`.
2. Register it in `__init__.py` — add a `ProviderInfo` with `client_class=MyProviderClient`, the right `credential_shape`, and a `summarizer_model` if a cheap sibling exists. Wrap registration in `try/except ImportError` if the SDK is optional.
3. Map the SDK's stop-reason strings to `StopReason` in a module-level `_STOP_MAP` constant.
4. Translate `Message` both ways in `_convert_messages`; if the tool-result role differs (like Codex's `function_call_output` vs OpenAI's `"tool"` role), match the provider's convention there.
5. Wire `with_retries` + a provider-specific error classifier + `is_context_overflow` into the client so the runtime gets uniform retry + overflow behavior.

No changes to `backend/domain/agent/`, `backend/domain/tools/`, or `backend/server/` — the adapter is the seam.
