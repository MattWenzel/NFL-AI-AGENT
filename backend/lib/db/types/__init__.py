"""Plain-Python value types produced or consumed by the SQL tier.

Invariant: this subtree imports nothing from SQLAlchemy or SQLModel. Verify
with `grep -rE 'sqlalchemy|sqlmodel' backend/lib/db/types/` (must be empty).
"""

from backend.lib.db.types.audit_events import AuditEvent
from backend.lib.db.types.errors import IdentityConflictError

__all__ = ["AuditEvent", "IdentityConflictError"]
