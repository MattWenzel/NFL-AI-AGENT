"""SQLite persistence layer.

Two trees inside:

- `sql/` — everything that imports SQLAlchemy/SQLModel. Tables, the
  `RuntimeStore` facade, per-domain query mixins, the engine, migrations.
- `types/` — plain Python value types (enums, exceptions) consumed by
  features. No SQL imports — verifiable via `grep "sqlalchemy\\|sqlmodel"
  backend/lib/db/types/`.

Public surface re-exported here so callers do
`from backend.lib.db import RuntimeStore, SessionRecord, AuditEvent` and
don't have to know which sub-tree something lives in.
"""

from backend.lib.db.sql.projections import SessionListEntry, SessionTranscript
from backend.lib.db.sql.store import RuntimeStore
from backend.lib.db.sql.tables import (
    AssistantPartRecord,
    AuthSessionRecord,
    CompactionSummaryRecord,
    EmailVerificationRecord,
    ExportRecord,
    LoginFailureRecord,
    SecurityEventRecord,
    SessionRecord,
    ToolRunRecord,
    TurnRecord,
    UserApiKeyRecord,
    UserIdentityRecord,
    UserRecord,
)
from backend.lib.db.types.audit_events import AuditEvent
from backend.lib.db.types.errors import IdentityConflictError

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
