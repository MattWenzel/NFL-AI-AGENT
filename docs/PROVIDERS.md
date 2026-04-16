# LLM Provider Abstraction Layer

Multi-provider LLM integration for the NFL stats chat agent. Supports Anthropic Claude and OpenAI GPT through a single typed abstraction.

- **Source:** `agent/providers/`
- **Canonical types:** `agent/providers/base.py`
- **Registry + factory:** `agent/providers/__init__.py`

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
- **No `api_key` on the base** — each provider's `__init__` takes its own `api_key` and passes it straight to the SDK client. The base class holds no auth state.
- **Error handling** — `_translate_error()` is defined once per provider (5-10 lines) mapping SDK exceptions to `LLMError`. `_wrap_api_errors()` is an async context manager on the base class that catches exceptions and delegates to `_translate_error()`. For `stream_message` (async generator), `_wrap_api_errors()` wraps stream creation; the iteration loop uses a manual try/except delegating to `_translate_error()` (can't yield inside a context manager).
- **Runtime is streaming-only** — `ChatRuntime.run_session` always consumes `stream_message`; `create_message` stays on the ABC as a convenience for direct callers but the runtime never invokes it. Non-streaming consumers (e.g. `/chat/message`) buffer the event stream at the API boundary.

---

## Registry and Factory

### `ProviderInfo` — Provider metadata

```python
@dataclass
class ProviderInfo:
    name: str                                   # "anthropic", "openai"
    display_name: str                           # "Anthropic", "OpenAI"
    env_key: str                                # "ANTHROPIC_API_KEY", "OPENAI_API_KEY"
    default_model: str                          # "claude-sonnet-4-20250514", etc.
    models: list[str]                           # Available model options
    context_window: int = 128_000               # Max input tokens
    max_output_tokens: int = 4096               # Max output tokens (passed to client)
    supports_streaming: bool = True
    supports_tools: bool = True
    client_class: type[BaseLLMClient] | None    # The concrete client class
```

### Current registry

| Provider | Default Model | Context | Max Output | Auth | SDK |
|----------|---------------|---------|------------|------|-----|
| `anthropic` | `claude-sonnet-4-20250514` | 200K | 4096 | `ANTHROPIC_API_KEY` | `anthropic` (required) |
| `openai` | `gpt-4o` | 128K | 4096 | `OPENAI_API_KEY` | `openai` (optional) |

Anthropic is always registered. OpenAI is registered only if its SDK is importable.

### `create_client()` — Factory function

```python
def create_client(provider=None, model=None, api_key=None) -> BaseLLMClient
```

Resolution chain:
1. **Provider** — `provider` arg, or `CHAT_PROVIDER` env var, or `"anthropic"`
2. **Model** — `model` arg, or `ProviderInfo.default_model`
3. **Key** — `api_key` arg, or `os.environ[info.env_key]`; raises `LLMError` if missing.
4. **Constructs** — `info.client_class(model=..., max_output_tokens=..., api_key=...)`

### `provider_is_available()` — Readiness check

```python
def provider_is_available(info: ProviderInfo) -> bool
```

Returns `True` when `os.environ[info.env_key]` is set. The `/chat/providers` endpoint and the CLI/UI use this to populate their `available` flag.

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

---

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
