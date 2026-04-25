"""Mixins for user accounts and auth-tier table CRUD.

All composed into `RuntimeStore` via multiple inheritance:

- UsersMixin              — users + user_api_keys + auth_sessions
- UserIdentitiesMixin     — user_identities (password / google / future)
- LoginFailuresMixin      — login_failures (per-email lockout)
- EmailVerificationMixin  — email_verification tokens
- SecurityEventsMixin     — security_events audit log
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from backend.persistence.errors import IdentityConflictError
from backend.persistence.models import (
    AuthSessionRecord,
    EmailVerificationRecord,
    ExportRecord,
    LoginFailureRecord,
    SecurityEventRecord,
    SessionRecord,
    UserApiKeyRecord,
    UserIdentityRecord,
    UserRecord,
    new_id,
    utcnow,
)


VERIFICATION_TTL_HOURS = 24


# ---------------- users + api keys + auth sessions ----------------

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

    async def purge_expired_auth_sessions(self) -> int:
        now = utcnow()
        async with self._async_session() as session:
            result = await session.execute(
                delete(AuthSessionRecord).where(AuthSessionRecord.expires_at < now)
            )
            await session.commit()
            return result.rowcount or 0


# ---------------- user_identities (password / google / future) ----------------

class UserIdentitiesMixin:
    """Per-user auth identities.

    One `users` row can have multiple identities — e.g. a password user
    who later links Google has two rows in `user_identities`. The
    `(provider, provider_subject)` pair is globally unique.
    """

    async def create_identity(
        self,
        *,
        user_id: int,
        provider: str,
        provider_subject: str,
        email: str | None = None,
    ) -> UserIdentityRecord:
        record = UserIdentityRecord(
            id=new_id(),
            user_id=user_id,
            provider=provider,
            provider_subject=provider_subject,
            email=email,
            created_at=utcnow(),
        )
        async with self._async_session() as session:
            session.add(record)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise IdentityConflictError(
                    f"{provider} identity '{provider_subject}' already linked"
                ) from exc
        return record

    async def get_user_by_identity(
        self, *, provider: str, provider_subject: str
    ) -> UserRecord | None:
        async with self._async_session() as session:
            result = await session.execute(
                select(UserRecord)
                .join(UserIdentityRecord, UserIdentityRecord.user_id == UserRecord.id)
                .where(
                    UserIdentityRecord.provider == provider,
                    UserIdentityRecord.provider_subject == provider_subject,
                )
            )
            return result.scalar_one_or_none()

    async def get_identity(
        self, *, user_id: int, provider: str
    ) -> UserIdentityRecord | None:
        async with self._async_session() as session:
            result = await session.execute(
                select(UserIdentityRecord).where(
                    UserIdentityRecord.user_id == user_id,
                    UserIdentityRecord.provider == provider,
                )
            )
            return result.scalar_one_or_none()

    async def list_identities_for_user(
        self, user_id: int
    ) -> list[UserIdentityRecord]:
        async with self._async_session() as session:
            result = await session.execute(
                select(UserIdentityRecord)
                .where(UserIdentityRecord.user_id == user_id)
                .order_by(UserIdentityRecord.created_at)
            )
            return list(result.scalars().all())

    async def delete_identity(self, *, user_id: int, provider: str) -> bool:
        async with self._async_session() as session:
            result = await session.execute(
                delete(UserIdentityRecord).where(
                    UserIdentityRecord.user_id == user_id,
                    UserIdentityRecord.provider == provider,
                )
            )
            await session.commit()
            return (result.rowcount or 0) > 0

    async def count_identities(self, user_id: int) -> int:
        async with self._async_session() as session:
            result = await session.execute(
                select(func.count())
                .select_from(UserIdentityRecord)
                .where(UserIdentityRecord.user_id == user_id)
            )
            return int(result.scalar_one())


# ---------------- login_failures (per-email lockout) ----------------

class LoginFailuresMixin:
    """Per-email failure counter for account lockout.

    Complements the per-IP rate limiter — the IP limit stops one address
    pounding login; this stops an IP-rotating attacker targeting one
    account. The counter resets on successful login (via
    `clear_login_failures`) and the service layer also resets on reads
    past `LOGIN_LOCKOUT_WINDOW_SECONDS` after the last failure.
    """

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


# ---------------- email verification tokens ----------------

class EmailVerificationMixin:
    """One-shot verification tokens for signup (and future password reset).

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


# ---------------- security_events (audit log) ----------------

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
