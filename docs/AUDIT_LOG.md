# Audit Log

Central record of code audit findings so future audits can skip already-examined items. Each round lists what was **fixed** (with commit ref) and what was **investigated but not actionable** (with the reason).

## How to use this log

1. Before starting a new audit, scan the "Not actionable" sections below — if your candidate matches an existing entry, skip it.
2. After an audit, append a new round at the top of the list with both fixed and not-actionable items.
3. Reference the commit hash that contains the fixes.

## Standing defenses (skip these categories)

These architectural properties are already in place. Candidates that rely on their absence are false positives:

| Defense | Why it's safe |
|---------|---------------|
| **Read-only database** | SQLite opened with `?mode=ro` in `sql_sandbox.py`; no writes possible |
| **SQL sandbox** | `execute_safe_sql` rejects DDL/DML keywords, enforces row limit, enforces timeout |
| **Parameterized SQL** | All user-facing queries use `?` placeholders (since `0b127f9`) |
| **Localhost-only API** | FastAPI binds to `127.0.0.1:8001`; not exposed to network |
| **No authentication needed** | Localhost + read-only = no auth surface; no secrets in DB |
| **Row-level truncation** | `_truncate_rows()` binary-searches to fit `TOOL_RESULT_MAX_CHARS`, preventing malformed JSON |
| **LIMIT capping** | `_ensure_limit()` caps or injects LIMIT clause; OFFSET preserved |
| **Async I/O wrapping** | All SQLite tool calls wrapped in `asyncio.to_thread()` (since `0b127f9`) |
| **Atomic conversation saves** | Write to tmp file + `os.replace()` for crash-safe persistence |
| **Error-safe conversation saves** | `_store.save` in `finally` block so partial progress survives errors |
| **Shared agent loop** | Single `run_agent_loop()` in `loop.py` eliminates divergence across CLI/message/stream paths |
| **Provider error wrapping** | `_wrap_api_errors()` in `BaseLLMClient` replaces per-provider try/except blocks |

---

## Audit rounds (newest first)

### Round 9 — 2026-02-22 — `38a58a2` Fix binary search empty rows and add per-conversation locking

#### Fixed
- **Binary search empty-row edge case** (`agent/tools.py`): `_truncate_rows()` could converge to `lo=0` when a single row exceeds `TOOL_RESULT_MAX_CHARS`, returning empty rows. Now falls back to a character-truncated preview of the first row.
- **Conversation race condition** (`agent/conversation.py`): Added `asyncio.Lock` per `conversation_id` in `ConversationStore` to prevent concurrent requests from interleaving message mutations.

#### Not actionable (false positives / already defended)
- SQL injection in tools — already parameterized (`0b127f9`)
- Unbounded query results — LIMIT capped by `_ensure_limit()`
- Missing input validation on chat endpoints — provider layer + sandbox handle malformed input
- File path traversal in CSV exports — filenames are server-generated UUIDs, not user-supplied
- Missing rate limiting — localhost-only, single-user tool
- No CSRF protection — no auth, no state-changing operations, localhost-only
- Conversation file corruption on crash — atomic writes already in place (`71a327e`)
- PBP database attach injection — attach path is a hardcoded constant, not user input
- Timeout missing on SQL — `execute_safe_sql` enforces `QUERY_TIMEOUT_SECONDS`
- Provider API key exposure — keys are env vars, never logged or returned in responses
- Missing Content-Security-Policy headers — localhost dev tool, no browser security boundary
- SSE stream resource leak — `request.is_disconnected()` check added in `698e39e`
- Unbounded conversation history — token-aware context windowing trims to fit provider limits
- JSON deserialization crashes — validated with fallbacks in `4f339c1`
- Missing request size limits — FastAPI/Uvicorn defaults apply, localhost-only
- Error messages leaking internals — read-only DB, no secrets to leak
- OpenAI streaming incomplete tool calls — handled on `finish_reason="length"` since `71dac03`
- Dead code in providers — cleaned up in `71a327e`
- Duplicate LIMIT injection with OFFSET — fixed in `3f9a3ee` and `698e39e`
- Tool result misattribution — fixed by index counter in `4f339c1`
- Missing async locks on file I/O — conversation saves are atomic (tmp+rename)
- Uncapped search_players results — LIMIT clamped to [1, 50] since `903a174`

### Round 8 — 2026-02-22 — `aecec63` Fix import fragility, robustness, observability, dead code, and DRY issues

#### Fixed
- **Lazy import fragility** (`agent/tools.py`): Moved `EXPORTS_DIR` import from inside function to top-level.
- **Non-integer limit crash** (`agent/tools.py`): `_search_players` now handles non-integer `limit` parameter gracefully.
- **Dropped tool results silent** (`api/routers/chat.py`): Added logging when tool results are dropped in `/chat/message`.
- **Redundant Content-Disposition header** (`api/routers/exports.py`): Removed duplicate header that `FileResponse` already sets.
- **Duplicated `format_file_size`** (`config.py`, `agent/tools.py`): Extracted to `config.py` as single source.
- **Null user text crash** (`agent/conversation.py`): Added guard for null text content during deserialization.

#### Not actionable
- Same standing defenses as Round 9 (read-only DB, sandboxed SQL, localhost-only, etc.)

### Round 7 — 2026-02-22 — `903a174` Fix negative LIMIT, crash risks, API inconsistencies, and DRY violations

#### Fixed
- **Negative LIMIT bypass** (`agent/tools.py`): `search_players` LIMIT clamped to [1, 50] so negative values can't return all rows.
- **Cleanup crash on locked file** (`api/routers/exports.py`): `cleanup_old_exports` now wraps per-file deletion in `try/except OSError`.
- **Introspect DB crash** (`agent/tools.py`): `_introspect_db` guards `conn.close()` with None check to avoid masking connect failures.
- **Missing truncation indicator** (`api/routers/chat.py`): Added `ChatResponse.truncated` field so clients know when iteration limit was hit.
- **Unused `request` param** (`api/routers/chat.py`): Removed from `chat_message` endpoint.
- **Silent unknown provider** (`agent/providers/__init__.py`): Now logs warning in `_set_context_window` instead of silent pass.
- **Dropped tool calls in OpenAI** (`agent/providers/openai_provider.py`): Logs warning on incomplete tool calls, fixes misleading count message.
- **Duplicated EXPORTS_DIR** (`config.py`): Consolidated from `tools.py` and `exports.py` into `config.py`.
- **Duplicated join-filter logic** (`agent/tools.py`): Extracted `_get_joins()` helper in schema registry.

#### Not actionable
- Same standing defenses apply.

### Round 6 — 2026-02-22 — `4f339c1` Fix tool-result pairing, provider robustness, deserialization validation, and stream logging

#### Fixed
- **Tool result misattribution** (`api/routers/chat.py`): Parallel tool results in `/chat/message` used `[-1]` index instead of tracking by position — replaced with index counter.
- **Conversation deserialization crashes** (`agent/conversation.py`): Added validation for id, messages, roles, and malformed entries.
- **Missing token usage logging** (all providers): Added to `stream_message()` in Anthropic and OpenAI providers.

#### Not actionable
- Same standing defenses apply.

### Round 5 — 2026-02-21 — `698e39e` Fix OFFSET preservation, tool_result merging, SSE disconnect, and context windowing

#### Fixed
- **OFFSET dropped by LIMIT injection** (`agent/sql_sandbox.py`): `_ensure_limit` now preserves OFFSET clause when capping LIMIT.
- **Consecutive tool_result role violation** (`agent/providers/anthropic_provider.py`): Merges consecutive `tool_result` messages into a single user message for Anthropic's strict role alternation.
- **SSE resource waste on disconnect** (`api/routers/chat.py`): Checks `request.is_disconnected()` in SSE stream to stop processing abandoned connections.
- **Orphaned tool_results in context windowing** (`agent/conversation.py`): Replaced per-message windowing with exchange-unit-based approach to keep assistant+tool_result pairs together.
- **23 regression tests** (`tests/test_fixes.py`): Added targeted tests covering all four fixes.

#### Not actionable
- Same standing defenses apply.

### Round 4 — 2026-02-21 — `3f9a3ee` Fix OFFSET handling, parallel tools, error consistency, and save-on-error

#### Fixed
- **Duplicate LIMIT with OFFSET** (`agent/sql_sandbox.py`): Expanded `_ensure_limit` regex to match `LIMIT...OFFSET` so queries like `LIMIT 100 OFFSET 50` don't get a duplicate LIMIT appended.
- **Sequential tool execution** (`agent/loop.py`): Tool calls now run via `asyncio.gather` instead of sequentially, enabling parallel execution of multi-tool iterations.
- **Inconsistent error format** (`agent/tools.py`): `_get_player_info` now returns `{"error": ...}` on primary lookup failure, consistent with all other tools.
- **Conversation lost on LLMError** (`api/routers/chat.py`): Moved `_store.save` into a `finally` block in `/message` so state persists even when errors interrupt mid-iteration.

#### Not actionable
- Same standing defenses apply.

### Round 3 — 2026-02-21 — `71dac03` Fix silent failure modes in SQL sandbox, streaming, and providers

#### Fixed
- **LIMIT passthrough above max** (`agent/sql_sandbox.py`): `_ensure_limit` now caps user-specified LIMIT values above `max_rows` instead of passing them through.
- **Conversation lost on stream error** (`api/routers/chat.py`): Stream endpoint now saves conversation on error so partial progress survives restarts.
- **Incomplete tool calls on truncation** (`agent/providers/openai_provider.py`): OpenAI streaming now emits accumulated tool calls on `finish_reason="length"` or missing finish.

#### Not actionable
- Same standing defenses apply.

### Round 2 — 2026-02-21 — `5a71ddf` DRY up agent architecture: shared loop, error handling, and config

#### Fixed
- **Duplicated `load_dotenv()`** (`config.py`): Extracted from `run.py` + `chat_cli.py` into `config.py`.
- **Magic context window formula in 3 places** (`agent/providers/__init__.py`): Added `ProviderInfo.effective_context_window` property.
- **Duplicated truncation logic** (`agent/tools.py`): Generalized `_truncate_rows()` to accept `**extra` keys, reused in `_execute_sql`.
- **8 try/except blocks across 3 providers** (`agent/providers/base.py`): Added `_wrap_api_errors()` / `_translate_error()` hook to `BaseLLMClient`.
- **3 independent tool-iteration loops** (`agent/loop.py`): Extracted `run_agent_loop()` async generator with typed events, unifying CLI, `/message`, and `/stream` paths.

#### Not actionable
- Same standing defenses apply. This was primarily a DRY/architecture round, not a security audit.

### Round 1 — 2026-02-21 — `0b127f9` Harden agent: parameterized SQL, async I/O, row-level truncation

#### Fixed
- **SQL injection via f-string interpolation** (`agent/tools.py`): Replaced f-string SQL in `_search_players` and `_get_player_info` with `?` placeholders.
- **Params support in sandbox** (`agent/sql_sandbox.py`): Added `params` tuple support to `execute_safe_sql` / `_run_sql`.
- **Blocking SQLite I/O in async path** (`agent/tools.py`): Wrapped all tool calls with `asyncio.to_thread()`.
- **Character-level truncation producing malformed JSON** (`agent/tools.py`): Switched to row-level truncation in `_execute_sql` and `_search_players`.
- **Silent JSON parse failures** (Anthropic/OpenAI providers): Added warning logs.
- **Dead httpx logger suppression** (`config.py`): Removed (httpx dependency already removed).
- **Static system prompt date** (`agent/system_prompt.py`): Made dynamic via `get_base_prompt()`.

#### Not actionable
- Same standing defenses apply. This was the foundational hardening round that established most of the standing defenses listed at the top.
