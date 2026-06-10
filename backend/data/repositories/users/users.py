"""Users, user_api_keys, and auth_sessions table CRUD."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.data.models import (
    AuthSessionRecord,
    ExportRecord,
    SessionRecord,
    UserApiKeyRecord,
    UserRecord,
    utcnow,
)


class UsersMixin:
    """Async CRUD for users, user_api_keys, and auth_sessions."""

    # ---------------- users ----------------

    async def count_users(self) -> int:
        async with self._async_session() as session:
            result = await session.execute(select(func.count()).select_from(UserRecord))
            return int(result.scalar_one())

    async def create_user(
        self,
        *,
        email: str,
        password_hash: str,
        role: str = "user",
        email_verified_at: str | None = None,
    ) -> UserRecord:
        now = utcnow()
        user = UserRecord(
            email=email.strip().lower(),
            password_hash=password_hash,
            role=role,
            email_verified_at=email_verified_at,
            created_at=now,
            updated_at=now,
        )
        async with self._async_session() as session:
            session.add(user)
            await session.commit()
        return user

    async def get_user_by_email(self, email: str) -> UserRecord | None:
        async with self._async_session() as session:
            result = await session.execute(
                select(UserRecord).where(UserRecord.email == email.strip().lower())
            )
            return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: int) -> UserRecord | None:
        async with self._async_session() as session:
            result = await session.execute(
                select(UserRecord).where(UserRecord.id == user_id)
            )
            return result.scalar_one_or_none()

    async def list_users(self) -> list[UserRecord]:
        async with self._async_session() as session:
            result = await session.execute(select(UserRecord).order_by(UserRecord.id.asc()))
            return list(result.scalars().all())

    async def mark_user_email_verified(self, user_id: int, verified_at: str) -> None:
        async with self._async_session() as session:
            await session.execute(
                update(UserRecord)
                .where(UserRecord.id == user_id)
                .values(email_verified_at=verified_at, updated_at=verified_at)
            )
            await session.commit()

    async def update_user_password(self, user_id: int, password_hash: str) -> None:
        now = utcnow()
        async with self._async_session() as session:
            await session.execute(
                update(UserRecord)
                .where(UserRecord.id == user_id)
                .values(password_hash=password_hash, updated_at=now)
            )
            await session.commit()

    async def delete_user(self, user_id: int) -> list[str]:
        """Full cascade delete of a user and all owned data.

        Returns the list of CSV filenames that were registered to this user
        so the caller can unlink them from disk — the DB row is gone by
        then.

        Order matters: sessions first (each via `delete_session` to cascade
        turns / parts / tool_runs / compaction_summaries), then exports,
        then the user row. auth_sessions and user_api_keys cascade
        automatically via the FK ON DELETE CASCADE declared on the models.
        """
        session_list = await self.list_sessions(user_id=user_id)
        session_ids = [s.id for s in session_list]
        for sid in session_ids:
            await self.delete_session(sid, user_id=user_id)
        exports = await self.list_exports(user_id=user_id)
        filenames = [e.filename for e in exports]
        async with self._async_session() as session:
            await session.execute(delete(ExportRecord).where(ExportRecord.user_id == user_id))
            await session.execute(delete(UserRecord).where(UserRecord.id == user_id))
            await session.commit()
        return filenames

    async def count_orphan_rows(self) -> dict[str, int]:
        """Rows with NULL user_id in user-scoped tables.

        Expected to be {0, 0} after first registration (backfill_orphan_ownership
        runs once). A non-zero result in multi-user mode means data is invisible
        to the scoped queries — surfaced as a startup warning.
        """
        async with self._async_session() as session:
            sessions_null = await session.execute(
                select(func.count()).select_from(SessionRecord).where(SessionRecord.user_id.is_(None))
            )
            exports_null = await session.execute(
                select(func.count()).select_from(ExportRecord).where(ExportRecord.user_id.is_(None))
            )
            return {
                "sessions": int(sessions_null.scalar_one()),
                "exports": int(exports_null.scalar_one()),
            }

    async def ensure_admin_exists(self) -> int | None:
        """Promote the oldest user to admin if no admin exists.

        Safety net for DBs that predate the `role` column (pre-multi-user
        registrations default to 'user' via ALTER TABLE). Returns the promoted
        user's id, or None if the invariant already held / there are no users.
        """
        async with self._async_session() as session:
            has_admin = await session.execute(
                select(UserRecord.id).where(UserRecord.role == "admin").limit(1)
            )
            if has_admin.scalar_one_or_none() is not None:
                return None
            oldest = await session.execute(
                select(UserRecord.id).order_by(UserRecord.id.asc()).limit(1)
            )
            oldest_id = oldest.scalar_one_or_none()
            if oldest_id is None:
                return None
            await session.execute(
                update(UserRecord).where(UserRecord.id == oldest_id).values(role="admin")
            )
            await session.commit()
            return int(oldest_id)

    async def backfill_orphan_ownership(self, user_id: int) -> tuple[int, int]:
        """Assign the given user_id to any sessions/exports that have none.

        Called once after first registration in single-user mode so pre-existing
        conversations/exports become owned by that user.
        """
        async with self._async_session() as session:
            s = await session.execute(
                update(SessionRecord).where(SessionRecord.user_id.is_(None)).values(user_id=user_id)
            )
            e = await session.execute(
                update(ExportRecord).where(ExportRecord.user_id.is_(None)).values(user_id=user_id)
            )
            await session.commit()
            return (s.rowcount or 0, e.rowcount or 0)

    # ---------------- user_api_keys ----------------

    async def upsert_api_key(
        self, *, user_id: int, provider: str, encrypted_key: str
    ) -> UserApiKeyRecord:
        now = utcnow()
        async with self._async_session() as session:
            existing = await session.execute(
                select(UserApiKeyRecord).where(
                    UserApiKeyRecord.user_id == user_id,
                    UserApiKeyRecord.provider == provider,
                )
            )
            existing_row = existing.scalar_one_or_none()
            created_at = existing_row.created_at if existing_row else now
            stmt = sqlite_insert(UserApiKeyRecord).values(
                user_id=user_id,
                provider=provider,
                encrypted_key=encrypted_key,
                created_at=created_at,
                updated_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id", "provider"],
                set_={"encrypted_key": encrypted_key, "updated_at": now},
            )
            await session.execute(stmt)
            await session.commit()
        return UserApiKeyRecord(
            user_id=user_id,
            provider=provider,
            encrypted_key=encrypted_key,
            created_at=created_at,
            updated_at=now,
        )

    async def delete_api_key(self, *, user_id: int, provider: str) -> bool:
        async with self._async_session() as session:
            result = await session.execute(
                delete(UserApiKeyRecord).where(
                    UserApiKeyRecord.user_id == user_id,
                    UserApiKeyRecord.provider == provider,
                )
            )
            await session.commit()
            return (result.rowcount or 0) > 0

    async def get_api_key(self, *, user_id: int, provider: str) -> UserApiKeyRecord | None:
        async with self._async_session() as session:
            result = await session.execute(
                select(UserApiKeyRecord).where(
                    UserApiKeyRecord.user_id == user_id,
                    UserApiKeyRecord.provider == provider,
                )
            )
            return result.scalar_one_or_none()

    async def list_api_keys(self, user_id: int) -> list[UserApiKeyRecord]:
        async with self._async_session() as session:
            result = await session.execute(
                select(UserApiKeyRecord)
                .where(UserApiKeyRecord.user_id == user_id)
                .order_by(UserApiKeyRecord.provider)
            )
            return list(result.scalars().all())

    async def user_has_api_key(self, *, user_id: int, provider: str) -> bool:
        async with self._async_session() as session:
            result = await session.execute(
                select(UserApiKeyRecord.user_id).where(
                    UserApiKeyRecord.user_id == user_id,
                    UserApiKeyRecord.provider == provider,
                )
            )
            return result.first() is not None

    # ---------------- auth_sessions ----------------

    async def create_auth_session(
        self, *, token: str, user_id: int, expires_at: str
    ) -> AuthSessionRecord:
        now = utcnow()
        record = AuthSessionRecord(
            token=token,
            user_id=user_id,
            created_at=now,
            expires_at=expires_at,
            last_used_at=now,
        )
        async with self._async_session() as session:
            session.add(record)
            await session.commit()
        return record

    async def get_auth_session(self, token: str) -> AuthSessionRecord | None:
        async with self._async_session() as session:
            result = await session.execute(
                select(AuthSessionRecord).where(AuthSessionRecord.token == token)
            )
            return result.scalar_one_or_none()

    async def touch_auth_session(
        self,
        token: str,
        *,
        min_interval_seconds: int = 0,
        last_used_at: str | None = None,
    ) -> bool:
        """Refresh last_used_at, optionally throttled by age.

        Returns True when a write occurred. Callers that already loaded the
        session can pass `last_used_at` to avoid a second SELECT.
        """
        if min_interval_seconds > 0:
            effective_last_used_at = last_used_at
            if effective_last_used_at is None:
                existing = await self.get_auth_session(token)
                if existing is None:
                    return False
                effective_last_used_at = existing.last_used_at
            try:
                parsed_last = datetime.fromisoformat(effective_last_used_at)
            except (TypeError, ValueError):
                parsed_last = None
            if parsed_last is not None:
                if parsed_last.tzinfo is None:
                    parsed_last = parsed_last.replace(tzinfo=timezone.utc)
                cutoff = datetime.now(timezone.utc) - timedelta(seconds=min_interval_seconds)
                if parsed_last >= cutoff:
                    return False
        async with self._async_session() as session:
            await session.execute(
                update(AuthSessionRecord)
                .where(AuthSessionRecord.token == token)
                .values(last_used_at=utcnow())
            )
            await session.commit()
        return True

    async def delete_auth_session(self, token: str) -> bool:
        async with self._async_session() as session:
            result = await session.execute(
                delete(AuthSessionRecord).where(AuthSessionRecord.token == token)
            )
            await session.commit()
            return (result.rowcount or 0) > 0

    async def invalidate_other_auth_sessions(self, user_id: int, keep_token: str) -> int:
        """Revoke every auth session for this user except the one in use.

        Called after a password change so stolen tokens can't outlive the
        rotation. Returns the number of tokens killed.
        """
        async with self._async_session() as session:
            result = await session.execute(
                delete(AuthSessionRecord).where(
                    AuthSessionRecord.user_id == user_id,
                    AuthSessionRecord.token != keep_token,
                )
            )
            await session.commit()
            return result.rowcount or 0

    async def invalidate_all_auth_sessions(self, user_id: int) -> int:
        """Revoke every auth session for this user — used after a password
        reset, where the resetter may not be the holder of existing
        sessions. Returns the number of tokens killed."""
        async with self._async_session() as session:
            result = await session.execute(
                delete(AuthSessionRecord).where(AuthSessionRecord.user_id == user_id)
            )
            await session.commit()
            return result.rowcount or 0

    async def purge_expired_auth_sessions(self) -> int:
        now = utcnow()
        async with self._async_session() as session:
            result = await session.execute(
                delete(AuthSessionRecord).where(AuthSessionRecord.expires_at < now)
            )
            await session.commit()
            return result.rowcount or 0
