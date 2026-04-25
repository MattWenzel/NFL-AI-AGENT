"""Per-email login failure tracking for account lockout.

Mixed into `RuntimeStore`. Complements the per-IP rate limiter in
server/rate_limit.py — the IP limit stops one address pounding login; this
stops an IP-rotating attacker targeting one account.

The counter resets on successful login (via `clear_login_failures`) and
also resets on reads past `LOGIN_LOCKOUT_WINDOW_SECONDS` after the last
failure (handled in the service layer, not here — we only persist raw state).
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.persistence.models import LoginFailureRecord, utcnow


class LoginFailuresMixin:
    async def get_login_failures(self, email: str) -> LoginFailureRecord | None:
        async with self._async_session() as session:
            result = await session.execute(
                select(LoginFailureRecord).where(LoginFailureRecord.email == email)
            )
            return result.scalar_one_or_none()

    async def record_login_failure(
        self,
        email: str,
        *,
        locked_until: str | None = None,
        reset_count: bool = False,
    ) -> LoginFailureRecord:
        """Increment failure counter (or reset to 1 if `reset_count=True`).

        `reset_count=True` is used by the service when the last failure is older
        than the rolling window — the counter starts fresh rather than stacking
        onto stale attempts.
        """
        now = utcnow()
        async with self._async_session() as session:
            existing = (
                await session.execute(
                    select(LoginFailureRecord).where(LoginFailureRecord.email == email)
                )
            ).scalar_one_or_none()
            if existing is None or reset_count:
                new_count = 1
            else:
                new_count = existing.failure_count + 1
            stmt = sqlite_insert(LoginFailureRecord).values(
                email=email,
                failure_count=new_count,
                last_failure_at=now,
                locked_until=locked_until,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["email"],
                set_={
                    "failure_count": new_count,
                    "last_failure_at": now,
                    "locked_until": locked_until,
                },
            )
            await session.execute(stmt)
            await session.commit()
        return LoginFailureRecord(
            email=email,
            failure_count=new_count,
            last_failure_at=now,
            locked_until=locked_until,
        )

    async def clear_login_failures(self, email: str) -> bool:
        async with self._async_session() as session:
            result = await session.execute(
                delete(LoginFailureRecord).where(LoginFailureRecord.email == email)
            )
            await session.commit()
            return (result.rowcount or 0) > 0
