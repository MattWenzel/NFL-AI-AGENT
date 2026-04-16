# nflverse API Documentation

NFL player statistics API covering **14 tables**, **2.25M+ rows**, seasons **1999-2025**, across 2 SQLite databases.

- **Base URL:** `http://localhost:8001`
- **Interactive docs:** `http://localhost:8001/docs`
- **Database files:** `NFLVERSE/data/nflverse.db` (main), `NFLVERSE/data/pbp.db` (play-by-play)

---

## Endpoint Reference

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Returns `{"status": "ok"}` |

### Chat

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/chat/providers` | List available LLM providers and models |
| `POST` | `/chat/message` | Send a message, get complete response |
| `POST` | `/chat/stream` | Send a message, stream response via SSE |
| `GET` | `/chat/conversations` | List all active conversations |
| `GET` | `/chat/conversations/{conversation_id}/transcript` | Get persisted transcript, tool runs, and compaction summaries |
| `DELETE` | `/chat/conversations/{conversation_id}` | Delete a conversation |

**ChatRequest** (body for `/chat/message` and `/chat/stream`):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | yes | User message (1-10,000 chars) |
| `conversation_id` | string | no | Existing conversation ID (omit to create new) |
| `provider` | string | no | LLM provider: `anthropic`, `openai` |
| `model` | string | no | Model name override (e.g. `gpt-4o`, `claude-sonnet-4-20250514`) |

**ChatResponse** (from `/chat/message`):

```json
{
  "conversation_id": "abc123",
  "response": "Patrick Mahomes threw for 4,183 yards in 2024...",
  "tool_calls": [
    {"tool": "execute_sql", "input": {"query": "..."}, "result_preview": "..."}
  ]
}
```

**SSE events** (from `/chat/stream`):

| Event type | Fields | Description |
|------------|--------|-------------|
| `conversation_id` | `id` | Sent first with the conversation ID |
| `text` | `text` | Streamed text chunk |
| `tool_call` | `name`, `input` | Tool invocation by the LLM |
| `tool_result` | `name` | Tool execution completed |
| `error` | `message` | Error message |
| `done` | — | Stream complete |

**ConversationInfo** (from `GET /chat/conversations`):

```json
[{"id": "abc123", "message_count": 5}]
```

**ConversationTranscriptResponse** (from `GET /chat/conversations/{conversation_id}/transcript`):

```json
{
  "session_id": "abc123",
  "turns": [
    {"id": "t1", "role": "user", "status": "completed", "text": "Who led the NFL in passing?", "compacted": false, "error": null}
  ],
  "tool_runs": [
    {"id": "tr1", "turn_id": "t2", "tool_name": "execute_sql", "status": "completed", "input": {"sql": "..."}, "result": "...", "error": null, "hint": null, "duration_ms": 42, "compacted": false}
  ],
  "summaries": [
    {"id": "c1", "summary_turn_id": "t9", "source_turn_ids": ["t1", "t2", "t3"]}
  ]
}
```

**ProviderResponse** (from `GET /chat/providers`):

```json
[{
  "name": "anthropic",
  "display_name": "Anthropic Claude",
  "models": ["claude-sonnet-4-20250514", "claude-haiku-4-5-20251001"],
  "default_model": "claude-sonnet-4-20250514",
  "available": true,
  "context_window": 200000,
  "supports_streaming": true,
  "supports_tools": true
}]
```

### Exports

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/exports/{filename}` | Download a CSV export file |

Filenames must match `[a-zA-Z0-9_-]+\.csv`. Files are auto-cleaned after 1 hour. Returns `404` if the file has expired or doesn't exist.

---

The chat runtime now persists transcripts in `data/runtime.sqlite3` as sessions, turns, assistant parts, tool runs, and compaction summaries. Long conversations are compacted by summarizing older turns and marking older raw tool output as compacted rather than deleting it.

For database schema, table documentation, join patterns, and query examples, see [DATABASE.md](DATABASE.md).
