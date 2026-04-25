"""Audit log for sensitive actions.

Mixed into `RuntimeStore`. `user_id` is nullable + ON DELETE SET NULL so
deleting an account doesn't wipe its audit trail — the row persists with
a null user reference, preserving a record of what happened.

Event types are open strings rather than an Enum so a new kind of event
can land without a schema change or model update. Callers should pass one
of the documented types from the security hardening plan for consistency.
"""

from __future__ import annotations

from sqlalchemy import select

from backend.core.persistence.models import SecurityEventRecord, new_id, utcnow


class SecurityEventsMixin:
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
