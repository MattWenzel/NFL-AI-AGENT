# LLM Provider Abstraction Layer

Multi-provider LLM integration for the NFL stats chat agent. Supports Anthropic Claude, OpenAI GPT, and ChatGPT Codex (OAuth) through a single typed abstraction.

- **Source:** `agent/providers/`
- **Canonical types:** `agent/providers/base.py`
- **Registry + factory:** `agent/providers/__init__.py`
- **OAuth support:** `agent/oauth/` (PKCE, token store, loopback capture, orchestrator)

---

## Canonical Types (The "Truth")

All providers conform to the types defined in `agent/providers/base.py`. These are the contracts that every provider must satisfy — consumers never see provider-specific types, wire formats, or SDK objects.

### `StopReason` — Why the model stopped

```python
class StopReason(str, Enum):
    END_TURN   = "end_turn"    # Model finished its response naturally
    TOOL_USE   = "tool_use"    # Model wants to call a tool
    MAX_TOKENS = "max_tokens"  # Hit the output token limit
```

Each provider maps its native stop reason to this enum via a module-level `_STOP_MAP` dict:

| StopReason | Anthropic | OpenAI |
|------------|-----------|--------|
| `END_TURN` | `"end_turn"` | `"stop"` |
| `TOOL_USE` | `"tool_use"` | `"tool_calls"` |
| `MAX_TOKENS` | `"max_tokens"` | `"length"` |

`StopReason` is a `str` subclass, so it serializes to JSON as a plain string (`"end_turn"`) and compares with `==` against string literals.

### `Usage` — Token consumption

```python
@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
```

Each provider normalizes its native field names:

| Usage field | Anthropic | OpenAI |
|-------------|-----------|--------|
| `input_tokens` | `response.usage.input_tokens` | `response.usage.prompt_tokens` |
| `output_tokens` | `response.usage.output_tokens` | `response.usage.completion_tokens` |

### `ToolDefinition` — Tool schema

```python
@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict  # JSON Schema

    def to_dict(self) -> dict       # -> Anthropic-format dict
    @classmethod
    def from_dict(cls, d) -> Self   # <- Anthropic-format dict
```

This is the canonical format for tool definitions. Uses the Anthropic convention (`name`, `description`, `input_schema`) as the internal representation. Each provider converts from `ToolDefinition` to its native wire format:

| Provider | Conversion | Wire format |
|----------|------------|-------------|
| Anthropic | `t.to_dict()` | `{"name", "description", "input_schema"}` — already native |
| OpenAI | `_convert_tools()` | `{"type": "function", "function": {"name", "description", "parameters"}}` |

Tool definitions are declared as raw dicts in `agent/tools.py` (`TOOL_DEFINITIONS`) for readability, then converted to typed form:

```python
# agent/tools.py
TOOL_DEFINITIONS = [{"name": "execute_sql", ...}, ...]   # raw dicts (kept for backward compat)
TOOLS: list[ToolDefinition] = [ToolDefinition.from_dict(d) for d in TOOL_DEFINITIONS]  # typed (preferred import)
```

Consumers import `TOOLS`, not `TOOL_DEFINITIONS`.

### `TextEvent` / `ToolUseEvent` — Streaming events

```python
@dataclass
class TextEvent:
    text: str

@dataclass
class ToolUseEvent:
    id: str      # Unique ID for this tool call (used to match results back)
    name: str    # Tool name (matches ToolDefinition.name)
    input: dict  # Parsed JSON arguments
```

These are yielded from `stream_message()` and collected in `MessageResponse.content`.

### `MessageResponse` — Complete response

```python
@dataclass
class MessageResponse:
    content: list          # list of TextEvent | ToolUseEvent
    stop_reason: StopReason
    usage: Usage = Usage()
```

Returned by `create_message()`. Every field is typed — no raw strings or dicts.

### `Message` — Provider-agnostic conversation message

```python
@dataclass
class Message:
    role: str                                  # "user", "assistant", "tool_result"
    text: str | None = None
    tool_calls: list[ToolUseEvent] | None = None
    tool_use_id: str | None = None
    tool_content: str | None = None
```

Three message shapes:

| Role | Fields used | Description |
|------|-------------|-------------|
| `user` | `text` | User's question |
| `assistant` | `text` and/or `tool_calls` | Model's response |
| `tool_result` | `tool_use_id`, `tool_content` | Result of executing a tool call |

Each provider converts `Message` lists to its native wire format in `_convert_messages()`. Key differences:

| Concept | Anthropic | OpenAI |
|---------|-----------|--------|
| System prompt | Separate `system` kwarg | `{"role": "system"}` message prepended |
| Assistant role | `"assistant"` | `"assistant"` |
| Tool result role | `"user"` with `type: "tool_result"` block | `"tool"` with `tool_call_id` |
| Tool call args | JSON object | JSON string |

### `LLMError` — User-facing error

```python
class LLMError(Exception): ...
```

Raised by providers for auth failures, rate limits, and API errors. Consumers catch this to display user-friendly messages without leaking SDK internals.

---

## `BaseLLMClient` — Abstract base class

```python
class BaseLLMClient(ABC):
    def __init__(self, model: str, *, max_output_tokens: int = 4096):
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.last_usage = Usage()

    async def create_message(messages, tools, system) -> MessageResponse    # abstract
    async def stream_message(messages, tools, system) -> AsyncIterator      # abstract
    provider_name: str                                                      # abstract property
    def _translate_error(self, exc) -> LLMError                             # abstract — maps SDK exceptions to LLMError
    async def _wrap_api_errors(self)                                        # context manager — delegates to _translate_error
    async def aclose(self) -> None                                          # release provider-owned resources (HTTP client, etc.)
```

Key design decisions:

- **`model` is required** — resolved by the factory from `ProviderInfo.default_model` before construction. No `_default_model()` method.
- **`max_output_tokens` flows from registry** — set per-provider in `ProviderInfo`, passed through by the factory. Providers use `self.max_output_tokens` in their API calls instead of hardcoded constants.
- **No `api_key` on the base** — each provider's `__init__` takes its own `api_key` (or `auth=` handle for OAuth) and passes it straight to the SDK client. The base class holds no auth state.
- **Error handling** — `_translate_error()` is defined once per provider (5-10 lines) mapping SDK exceptions to `LLMError`. `_wrap_api_errors()` is an async context manager on the base class that catches exceptions and delegates to `_translate_error()`. For `stream_message` (async generator), `_wrap_api_errors()` wraps stream creation; the iteration loop uses a manual try/except delegating to `_translate_error()` (can't yield inside a context manager).
- **Runtime is streaming-only** — `ChatRuntime.run_session` always consumes `stream_message`; `create_message` stays on the ABC as a convenience for direct callers but the runtime never invokes it. Non-streaming consumers (e.g. `/chat/message`) buffer the event stream at the API boundary.

---

## Registry and Factory

### `ProviderInfo` — Provider metadata

```python
AuthType = Literal["api_key", "oauth"]

@dataclass
class ProviderInfo:
    name: str                                   # "anthropic", "openai", "codex"
    display_name: str                           # "Anthropic", "OpenAI", "ChatGPT Codex"
    env_key: str | None                         # "ANTHROPIC_API_KEY", etc. (None for OAuth)
    default_model: str                          # "claude-sonnet-4-20250514", etc.
    models: list[str]                           # Available model options
    context_window: int = 128_000               # Max input tokens
    max_output_tokens: int = 4096               # Max output tokens (passed to client)
    supports_streaming: bool = True
    supports_tools: bool = True
    client_class: type[BaseLLMClient] | None    # The concrete client class
    auth_type: AuthType = "api_key"             # "oauth" providers skip env_key
```

### Current registry

| Provider | Default Model | Context | Max Output | Auth | SDK |
|----------|---------------|---------|------------|------|-----|
| `anthropic` | `claude-sonnet-4-20250514` | 200K | 4096 | `ANTHROPIC_API_KEY` | `anthropic` (required) |
| `openai` | `gpt-4o` | 128K | 4096 | `OPENAI_API_KEY` | `openai` (optional) |
| `codex` | `gpt-5.1-codex` | 200K | 8192 | OAuth (PKCE) | `httpx` (always available) |

Anthropic is always registered. OpenAI and Codex are registered only if their dependencies are importable.

### `create_client()` — Factory function

```python
def create_client(provider=None, model=None, api_key=None) -> BaseLLMClient
```

Resolution chain:
1. **Provider** — `provider` arg, or `CHAT_PROVIDER` env var, or `"anthropic"`
2. **Model** — `model` arg, or `ProviderInfo.default_model`
3. **Auth** — branches on `info.auth_type`:
   - `"api_key"` — `api_key` arg, or `os.environ[info.env_key]`; raises `LLMError` if missing.
   - `"oauth"` — loads `TokenStore(CODEX_AUTH_PATH)`; raises `LLMError("... not authenticated")` if there is no stored record; constructs a `CodexAuth` and passes it as `auth=...` to the client.
4. **Constructs** — `info.client_class(model=..., max_output_tokens=..., api_key=...|auth=...)`

### `provider_is_available()` — Readiness check

```python
def provider_is_available(info: ProviderInfo) -> bool
```

Returns `True` when the provider has everything it needs to build a client now. For api-key providers this is `os.environ[info.env_key]`; for OAuth providers it is `TokenStore(CODEX_AUTH_PATH).has_record()`. The `/chat/providers` endpoint and the CLI/UI use this to populate their `available` flag.

### Public API

```python
register_provider(info: ProviderInfo) -> None       # Add a provider
get_provider(name: str) -> ProviderInfo              # Lookup (raises KeyError)
list_providers() -> list[ProviderInfo]               # All registered
get_default_provider() -> str                        # From env or "anthropic"
create_client(provider, model, api_key) -> BaseLLMClient  # Factory
provider_is_available(info: ProviderInfo) -> bool    # Ready-to-use check
```

All types are re-exported from `agent/providers/__init__.py`:

```python
from agent.providers import (
    BaseLLMClient, LLMError, Message, MessageResponse,
    TextEvent, ToolUseEvent, ProviderInfo,
    StopReason, Usage, ToolDefinition,
    create_client, get_provider, list_providers, get_default_provider,
    provider_is_available,
)
```

---

## Provider Implementations

Each provider follows an identical internal structure:

```
_STOP_MAP: dict[str, StopReason]          # Native stop reason -> StopReason

class XxxClient(BaseLLMClient):
    __init__(model, *, max_output_tokens, api_key)
    provider_name -> str                   # Property
    _translate_error(exc) -> LLMError      # SDK exception -> LLMError (one definition)

    create_message(messages, tools, system) -> MessageResponse
    stream_message(messages, tools, system) -> AsyncIterator

    _convert_messages(messages) -> native   # Message -> wire format
    _convert_tools(tools) -> native         # ToolDefinition -> wire format
    _build_kwargs/config(...)               # Assemble API call parameters
    _parse_response(response) -> MessageResponse  # Wire format -> canonical types
```

### Anthropic (`anthropic_provider.py`)

- SDK: `anthropic.AsyncAnthropic`
- Streaming: Uses `client.messages.stream()` context manager with `content_block_start/delta/stop` events
- Tools: `t.to_dict()` — already native format
- Stop reason: Direct string mapping, always present

### OpenAI (`openai_provider.py`)

- SDK: `openai.AsyncOpenAI`
- Streaming: `client.chat.completions.create(stream=True)` with delta accumulation
- Tools: Wrapped in `{"type": "function", "function": {...}}` envelope
- System prompt: Prepended as `{"role": "system"}` message (not a separate parameter)
- Tool call streaming: Accumulated by index across chunks, emitted on `finish_reason`

### Codex (`codex_provider.py`)

- Transport: raw `httpx.AsyncClient` (no OpenAI SDK — the Responses wire shape differs from chat-completions)
- Endpoint: `POST https://chatgpt.com/backend-api/codex/responses`
- Auth: `CodexAuth` injected via `auth=` kwarg (not `api_key`). Token + `chatgpt-account-id` decoded from the access-token JWT on every request.
- Required headers: `Authorization: Bearer <token>`, `chatgpt-account-id`, `OpenAI-Beta: responses=experimental`, `originator: pi`.
- Message shape: canonical `Message` → `input` array where assistant text and tool calls are **separate items** (`{"type":"message"}` + `{"type":"function_call"}`); tool results are `{"type":"function_call_output"}` with a JSON-string `output`.
- Tools: `_strictify_schema()` recursively rewrites JSON Schemas to satisfy Codex strict mode — `additionalProperties: false` on every object, every property in `required`, optionals made nullable (`["T", "null"]` for simple types, append `{"type":"null"}` for `anyOf`/`oneOf`, `anyOf` wrapper otherwise). Anthropic/OpenAI schemas are untouched.
- Streaming: SSE with a state machine that handles `response.output_text.delta`, `response.output_item.added` (registers `item_id → call_id`), `response.function_call_arguments.delta|done`, `response.output_item.done`, and a `response.done`/`completed` fallback that sweeps `response.output` for any tool calls not emitted via deltas.
- Errors: `_translate_error` maps `httpx.HTTPStatusError` (401 → re-auth, 429 → rate-limited), `httpx.RequestError`, and `CodexAuthError` to `LLMError`.

---

## OAuth Subsystem (`agent/oauth/`)

Supports providers with `auth_type="oauth"` by maintaining on-disk tokens and driving the OAuth+PKCE flow. Today this powers only Codex but is deliberately provider-agnostic.

### Modules

| File | Role |
|------|------|
| `pkce.py` | `generate_pkce() -> PKCEChallenge(verifier, challenge, state)`. Base64url-encoded, padding stripped, per RFC 7636. |
| `jwt_decode.py` | Unverified payload decode. `email_from_id_token()`, `chatgpt_account_id_from_access_token()`. The auth server signs; we only read claims. |
| `token_store.py` | `TokenStore(path)` → atomic JSON at `data/codex_auth.json`. Keyed by email with a `default_email` pointer; single-user today, multi-account later is a drop-in. |
| `codex_auth.py` | `CodexAuth(store)` orchestrator. `build_authorize_request()` → `{url, state, verifier}`; `exchange_code(code, verifier)` POSTs to `auth.openai.com/oauth/token`; `refresh(record)` rotates tokens (preserves old `refresh_token` if response omits one); `get_valid_token()` is the single entry point callers use on every request — refreshes when `now > expires_at - 30s` and returns `(access_token, chatgpt_account_id)`. |
| `login_server.py` | `run_loopback_capture(port=1455, path="/auth/callback", timeout=300)` — short-lived `http.server` on 127.0.0.1 that captures the first OAuth callback and shuts down. Required because the public Codex CLI OAuth client is registered against `http://localhost:1455/auth/callback`. |

### Flow (both CLI and UI)

```
client → build_authorize_request() → {url, state, verifier}
client → open browser to `url`
client → run_loopback_capture()        # background task for the API
user    → authorizes at auth.openai.com
browser → GET http://localhost:1455/auth/callback?code=...&state=...
server  → validates state matches, exchange_code(code, verifier)
auth    → POST auth.openai.com/oauth/token (PKCE)
store   → TokenStore.save(record)       # data/codex_auth.json
```

The CLI (`chat_cli.py login --provider codex`) runs this inline. The API exposes `POST /auth/codex/login` + `GET /auth/codex/status` + `POST /auth/codex/logout` (`api/routers/auth.py`) so the browser UI can drive the same flow — `start_login` schedules the loopback capture + exchange as a background task and the UI polls `/status` until `authenticated=true`.

Only one process can bind port 1455 at a time: if the API server is running, use the UI; if not, use the CLI.

### Tokens

`data/codex_auth.json` holds `{accounts: {<email>: {access_token, refresh_token, expires_at_ms, id_token, email}}, default_email}`. The `chatgpt_account_id` is **not** persisted — it is decoded from the access-token JWT on every request so rotated accounts never stale. File is atomic-written via `tmp + os.replace`.

## Data Flow

### Request path (streaming)

```
User question
  -> RuntimeStore.create_turn(role="user")
  -> ChatRuntime.run_session(session, user_text, client, tools, provider_name)
      -> RuntimeStore.build_model_messages(session.id) -> list[Message]
      -> client.stream_message                          # runtime is streaming-only
          -> provider._convert_messages(messages) -> native wire format
          -> provider._convert_tools(tools)       -> native wire format
          -> provider._build_kwargs/config(...)
          -> SDK streaming API call
          -> yield TextEvent / ToolUseEvent
      -> persist assistant text/tool calls as turns, parts, and tool_runs
      -> execute tools and persist tool_result parts
      -> compact older turns/tool outputs when context budget is exceeded
      -> emit RuntimeEvent objects
  -> Consumer maps runtime events to output (terminal ANSI, JSON, SSE)
```

The shared runtime lives in `agent/runtime.py` and is the single source of truth for the tool-iteration cycle. Consumers (CLI, `/message`, `/stream`) just map the runtime events to their output format.

### Response path (non-streaming)

```
SDK response object
  -> provider._parse_response(response)
      -> normalize content blocks -> list[TextEvent | ToolUseEvent]
      -> _STOP_MAP[native_reason] -> StopReason
      -> native usage -> Usage(input_tokens, output_tokens)
  -> MessageResponse(content, stop_reason, usage)
```

### Transcript state

`agent/runtime_store.py` stores sessions, turns, assistant parts, tool runs, and compaction summaries in SQLite. It builds provider-facing `Message` objects from the active transcript, omitting compacted raw turns/tool output while keeping the full transcript in storage. Context budget is set per-provider via `info.effective_context_window` (75% of max), and `agent/runtime.py` compacts older turns when the active prompt grows too large.

---

## Supporting Modules

### `agent/tools.py` — Tool definitions and execution

Defines 5 tools: `search_players`, `execute_sql`, `get_schema`, `get_player_info`, `create_csv_export`.

Exports:
- `TOOL_DEFINITIONS` — Raw Anthropic-format dicts (kept for backward compat)
- `TOOLS: list[ToolDefinition]` — Typed form (preferred import for consumers)
- `execute_tool(name, input_data) -> str` — Dispatch and execute

### `agent/provider_hints.py` — Per-provider prompt supplements

Some models sometimes skip instructions Claude follows reliably. Extra reminders are appended to the system prompt only for providers that need them (`openai`). No hints for `anthropic`.

```python
get_system_prompt(provider: str) -> str  # SYSTEM_PROMPT + optional hints
```

### `agent/system_prompt.py` — Shared system prompt

Condensed database knowledge (~5K tokens) shared across all providers. Contains table descriptions, join patterns, and query conventions.

---

## Adding a New Provider

### API-key provider (standard case)

1. Create `agent/providers/newprovider_provider.py`:
   - Subclass `BaseLLMClient`
   - Implement `create_message`, `stream_message`, `provider_name`, `_translate_error`
   - Use `async with self._wrap_api_errors():` around SDK calls in `create_message` and stream creation in `stream_message`
   - Define `_STOP_MAP` for the provider's native stop reasons
   - Convert `list[ToolDefinition]` to the provider's native tool format in `_convert_tools()`
   - Return `Usage(input_tokens=..., output_tokens=...)` in `_parse_response()`
   - Return `StopReason` (via `_STOP_MAP`) in `_parse_response()`
   - Use `self.max_output_tokens` in API calls (not a hardcoded constant)

2. Register in `agent/providers/__init__.py`:
   ```python
   try:
       from agent.providers.newprovider_provider import NewClient
       register_provider(ProviderInfo(
           name="newprovider",
           default_model="...",
           max_output_tokens=...,
           client_class=NewClient,
           ...
       ))
   except ImportError:
       logger.debug("New SDK not installed")
   ```

3. Optionally add provider-specific hints in `agent/provider_hints.py`.

No changes needed in consumers (`chat_cli.py`, `api/routers/chat.py`, `runtime.py`, `tools.py`).

### OAuth provider

Follow the api-key steps above, with these differences:

1. The client's `__init__` takes an auth handle instead of `api_key` (e.g. `auth: CodexAuth`). Call `super().__init__(model, max_output_tokens=...)`.
2. Register with `auth_type="oauth"` and leave `env_key` unset (defaults to `None`):
   ```python
   register_provider(ProviderInfo(
       name="newoauth", auth_type="oauth", env_key=None,
       client_class=NewOAuthClient, ...,
   ))
   ```
3. Extend `create_client()` in `agent/providers/__init__.py` to load your provider's auth handle in the `auth_type == "oauth"` branch. Keep the token-store handling in `agent/oauth/` so it is reusable across OAuth providers.
4. Add an API router under `api/routers/` exposing `POST /auth/<name>/login`, `GET /status`, `POST /logout`. Include it in `api/main.py`.
5. Teach the UI (`chat.html`) the new provider name if it needs a branded button (today `renderAuthWidget` is provider-agnostic — it renders for any `auth_type=="oauth"` provider).
