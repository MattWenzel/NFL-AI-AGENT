"""SQLite persistence layer.

Public surface: `RuntimeStore` plus the SQLModel table classes it returns.
Callers import from here rather than the per-domain submodules so the
internal split (users/transcripts/exports/models/engine) stays a private
implementation detail.
"""

from storage.models import (
    AssistantPartRecord,
    AuthSessionRecord,
    CompactionSummaryRecord,
    EmailVerificationRecord,
    ExportRecord,
    LoginFailureRecord,
    SecurityEventRecord,
    SessionListEntry,
    SessionRecord,
    SessionTranscript,
    ToolRunRecord,
    TurnRecord,
    UserApiKeyRecord,
    UserIdentityRecord,
    UserRecord,
)
from storage.audit_events import AuditEvent
from storage.errors import IdentityConflictError
from storage.store import RuntimeStore

__all__ = [
    "RuntimeStore",
    "AssistantPartRecord",
    "AuditEvent",
    "AuthSessionRecord",
    "CompactionSummaryRecord",
    "EmailVerificationRecord",
    "ExportRecord",
    "IdentityConflictError",
    "LoginFailureRecord",
    "SecurityEventRecord",
    "SessionListEntry",
    "SessionRecord",
    "SessionTranscript",
    "ToolRunRecord",
    "TurnRecord",
    "UserApiKeyRecord",
    "UserIdentityRecord",
    "UserRecord",
]
