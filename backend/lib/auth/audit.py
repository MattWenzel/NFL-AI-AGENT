"""Shared audit context and security-event logging.

Each call writes a structured row to the `security_events` table via
the store AND emits a parallel structured log line to the
`security_events` logger so real-time observers see the event without
tailing the database.

DB-write failures are logged at ERROR but don't bubble — audit emission
must never block the main flow it's annotating.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.lib.db import AuditEvent, RuntimeStore

logger = logging.getLogger(__name__)
_event_logger = logging.getLogger("security_events")


@dataclass(frozen=True)
class AuditContext:
    """Request-scoped audit metadata passed from routes to services."""

    ip: str | None = None
    user_agent: str | None = None


async def audit_log(
    store: RuntimeStore,
    event_type: AuditEvent,
    user_id: int | None,
    audit: AuditContext,
    metadata: dict,
) -> None:
    try:
        await store.record_security_event(
            event_type=event_type,
            user_id=user_id,
            ip=audit.ip,
            user_agent=audit.user_agent,
            metadata=metadata,
        )
    except Exception:  # noqa: BLE001 — audit failures shouldn't block the main flow
        logger.exception("Failed to record %s audit event", event_type)
    _event_logger.info(
        "security_event",
        extra={
            "event_type": event_type,
            "user_id": user_id,
            "ip": audit.ip,
            "metadata": metadata,
        },
    )
