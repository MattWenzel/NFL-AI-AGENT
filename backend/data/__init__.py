"""Runtime persistence package.

Canonical imports for persisted application data live here:

- `models.py` defines SQLModel table classes.
- `repositories/` contains CRUD/query mixins.
- `store.py` composes those mixins into `RuntimeStore`.
- `types/` contains SQL-free value types shared with services.
"""

from backend.data.projections import SessionListEntry, SessionTranscript
from backend.data.store import RuntimeStore
from backend.data.models import (
    AssistantPartRecord,
    AuthSessionRecord,
    CompactionSummaryRecord,
    EmailVerificationRecord,
    ExportRecord,
    LoginFailureRecord,
    SecurityEventRecord,
    SessionRecord,
    TableStateRecord,
    ToolRunRecord,
    TurnRecord,
    UserApiKeyRecord,
    UserIdentityRecord,
    UserRecord,
)
from backend.data.types.audit_events import AuditEvent
from backend.data.types.errors import IdentityConflictError

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
    "TableStateRecord",
    "ToolRunRecord",
    "TurnRecord",
    "UserApiKeyRecord",
    "UserIdentityRecord",
    "UserRecord",
]
