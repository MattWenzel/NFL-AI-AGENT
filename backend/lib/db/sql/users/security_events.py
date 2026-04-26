"""security_events CRUD: audit log."""

from __future__ import annotations

from sqlalchemy import select

from backend.lib.db.sql.tables import SecurityEventRecord, new_id, utcnow


class SecurityEventsMixin:
    """Audit log for sensitive actions.

    `user_id` is nullable + ON DELETE SET NULL so deleting an account
    doesn't wipe its audit trail — the row persists with a null user
    reference. `event_type` is an open string (no FK / Enum at the DB
    level) so a new event kind can land without a schema change; callers
    should use the `AuditEvent` enum from `audit_events.py` for
    typo-protection.
    """

    async def record_security_event(
        self,
        *,
        event_type: str,
        user_id: int | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        metadata: dict | None = None,
    ) -> SecurityEventRecord:
        record = SecurityEventRecord(
            id=new_id(),
            user_id=user_id,
            event_type=event_type,
            ip=ip,
            user_agent=user_agent,
            event_metadata=metadata or {},
            created_at=utcnow(),
        )
        async with self._async_session() as session:
            session.add(record)
            await session.commit()
        return record

    async def list_security_events_for_user(
        self, user_id: int, *, limit: int = 100
    ) -> list[SecurityEventRecord]:
        async with self._async_session() as session:
            result = await session.execute(
                select(SecurityEventRecord)
                .where(SecurityEventRecord.user_id == user_id)
                .order_by(SecurityEventRecord.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())
