"""email_verification CRUD: one-shot tokens for signup (and future password reset)."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update

from backend.data.models import EmailVerificationRecord, utcnow


VERIFICATION_TTL_HOURS = 24


class EmailVerificationMixin:
    """One-shot verification tokens.

    `consume_verification` atomically verifies the token is unexpired and
    unused, marks it used, and returns the user_id. The used_at column
    retains consumed rows for audit visibility — call `purge_expired`
    periodically to trim.
    """

    async def create_verification(
        self,
        *,
        user_id: int,
        purpose: str = "signup",
        ttl_hours: int = VERIFICATION_TTL_HOURS,
    ) -> EmailVerificationRecord:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        record = EmailVerificationRecord(
            token=token,
            user_id=user_id,
            purpose=purpose,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(hours=ttl_hours)).isoformat(),
        )
        async with self._async_session() as session:
            session.add(record)
            await session.commit()
        return record

    async def consume_verification(self, token: str, *, purpose: str = "signup") -> int | None:
        """Atomically mark a token used and return its user_id.

        Returns None if the token is missing, wrong purpose, already used, or
        expired. The atomicity matters under concurrent clicks on the same
        verification link.
        """
        now = utcnow()
        async with self._async_session() as session:
            result = await session.execute(
                update(EmailVerificationRecord)
                .where(
                    EmailVerificationRecord.token == token,
                    EmailVerificationRecord.purpose == purpose,
                    EmailVerificationRecord.used_at.is_(None),
                    EmailVerificationRecord.expires_at > now,
                )
                .values(used_at=now)
                .returning(EmailVerificationRecord.user_id)
            )
            user_id = result.scalar_one_or_none()
            await session.commit()
            return int(user_id) if user_id is not None else None

    async def get_latest_verification(
        self, *, user_id: int, purpose: str = "signup"
    ) -> EmailVerificationRecord | None:
        async with self._async_session() as session:
            result = await session.execute(
                select(EmailVerificationRecord)
                .where(
                    EmailVerificationRecord.user_id == user_id,
                    EmailVerificationRecord.purpose == purpose,
                )
                .order_by(EmailVerificationRecord.created_at.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()

    async def purge_expired_verifications(self) -> int:
        now = utcnow()
        async with self._async_session() as session:
            result = await session.execute(
                delete(EmailVerificationRecord).where(
                    EmailVerificationRecord.expires_at < now
                )
            )
            await session.commit()
            return result.rowcount or 0
