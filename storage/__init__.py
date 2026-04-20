"""SQLite persistence layer.

Public surface: `RuntimeStore` plus the dataclass records it returns.
Callers import from here rather than the per-domain submodules so the
internal split (users/transcripts/exports/schema/_rows) stays a private
implementation detail.
"""

from storage.records import (
    AssistantPartRecord,
    AuthSessionRecord,
    CompactionSummaryRecord,
    ExportRecord,
    SessionRecord,
    SessionTranscript,
    ToolRunRecord,
    TurnRecord,
    UserApiKeyRecord,
    UserRecord,
    safe_load_tool_input,
)
from storage.store import RuntimeStore

__all__ = [
    "RuntimeStore",
    "AssistantPartRecord",
    "AuthSessionRecord",
    "CompactionSummaryRecord",
    "ExportRecord",
    "SessionRecord",
    "SessionTranscript",
    "ToolRunRecord",
    "TurnRecord",
    "UserApiKeyRecord",
    "UserRecord",
    "safe_load_tool_input",
]
