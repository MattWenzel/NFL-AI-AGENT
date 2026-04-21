"""Domain-focused repository adapters over the sqlite runtime store."""

from __future__ import annotations

from dataclasses import dataclass

from storage import RuntimeStore, SessionRecord, UserApiKeyRecord


@dataclass(frozen=True)
class ConversationListEntry:
    id: str
    turn_count: int
    title: str
    provider: str | None
    model: str | None
    updated_at: str | None
    pinned_at: str | None
    source_csv_id: str | None

    @classmethod
    def from_row(cls, row: dict) -> "ConversationListEntry":
        return cls(
            id=row["id"],
            turn_count=row["turn_count"],
            title=row["title"],
            provider=row.get("provider"),
            model=row.get("model"),
            updated_at=row.get("updated_at"),
            pinned_at=row.get("pinned_at"),
            source_csv_id=row.get("source_csv_id"),
        )


@dataclass(frozen=True)
class UserRepository:
    _store: RuntimeStore

    def count_users_sync(self) -> int:
        return self._store.count_users()

    async def count_users(self) -> int:
        return await self._store.count_users_async()

    async def create_user(
        self,
        *,
        email: str,
        password_hash: str,
        role: str = "user",
        email_verified_at: str | None = None,
    ):
        return await self._store.create_user_async(
            email=email,
            password_hash=password_hash,
            role=role,
            email_verified_at=email_verified_at,
        )

    async def get_user_by_email(self, email: str):
        return await self._store.get_user_by_email_async(email)

    async def get_user_by_id(self, user_id: int):
        return await self._store.get_user_by_id_async(user_id)

    async def update_user_password(self, user_id: int, password_hash: str) -> None:
        await self._store.update_user_password_async(user_id, password_hash)

    async def delete_user(self, user_id: int) -> list[str]:
        return await self._store.delete_user_async(user_id)

    async def backfill_orphan_ownership(self, user_id: int) -> tuple[int, int]:
        return await self._store.backfill_orphan_ownership_async(user_id)

    def ensure_admin_exists_sync(self):
        return self._store.ensure_admin_exists()

    async def ensure_admin_exists(self):
        return await self._store.ensure_admin_exists_async()

    def count_orphan_rows_sync(self) -> dict[str, int]:
        return self._store.count_orphan_rows()

    async def count_orphan_rows(self) -> dict[str, int]:
        return await self._store.count_orphan_rows_async()

    def purge_expired_auth_sessions_sync(self) -> int:
        return self._store.purge_expired_auth_sessions()

    async def purge_expired_auth_sessions(self) -> int:
        return await self._store.purge_expired_auth_sessions_async()

    async def upsert_api_key(
        self,
        *,
        user_id: int,
        provider: str,
        encrypted_key: str,
    ) -> UserApiKeyRecord:
        return await self._store.upsert_api_key_async(
            user_id=user_id,
            provider=provider,
            encrypted_key=encrypted_key,
        )

    async def delete_api_key(self, *, user_id: int, provider: str) -> bool:
        return await self._store.delete_api_key_async(user_id=user_id, provider=provider)

    async def get_api_key(self, *, user_id: int, provider: str):
        return await self._store.get_api_key_async(user_id=user_id, provider=provider)

    async def list_api_keys(self, user_id: int):
        return await self._store.list_api_keys_async(user_id)

    async def create_auth_session(
        self,
        *,
        token: str,
        user_id: int,
        expires_at: str,
    ):
        return await self._store.create_auth_session_async(
            token=token,
            user_id=user_id,
            expires_at=expires_at,
        )

    async def get_auth_session(self, token: str):
        return await self._store.get_auth_session_async(token)

    async def touch_auth_session(
        self,
        token: str,
        *,
        min_interval_seconds: int = 0,
        last_used_at: str | None = None,
    ):
        return await self._store.touch_auth_session_async(
            token,
            min_interval_seconds=min_interval_seconds,
            last_used_at=last_used_at,
        )

    async def delete_auth_session(self, token: str) -> bool:
        return await self._store.delete_auth_session_async(token)

    async def invalidate_other_auth_sessions(self, user_id: int, *, keep_token: str) -> int:
        return await self._store.invalidate_other_auth_sessions_async(user_id, keep_token=keep_token)


@dataclass(frozen=True)
class ConversationRepository:
    _store: RuntimeStore

    async def get_session(self, session_id: str | None, *, user_id: int | None = None):
        return await self._store.get_session_async(session_id, user_id=user_id)

    async def list_sessions(self, *, user_id: int | None = None):
        rows = await self._store.list_sessions_async(user_id=user_id)
        return [ConversationListEntry.from_row(row) for row in rows]

    async def get_transcript(self, session_id: str):
        return await self._store.get_transcript_async(session_id)

    async def update_session(self, session: SessionRecord) -> None:
        await self._store.update_session_async(session)

    async def set_session_pinned(self, session_id: str, pinned: bool, *, user_id: int | None = None):
        return await self._store.set_session_pinned_async(session_id, pinned, user_id=user_id)

    async def delete_session(self, session_id: str, *, user_id: int | None = None) -> bool:
        return await self._store.delete_session_async(session_id, user_id=user_id)

    async def get_or_create_session(
        self,
        session_id: str | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
        context_window: int = 0,
        user_id: int | None = None,
    ):
        return await self._store.get_or_create_session_async(
            session_id,
            provider=provider,
            model=model,
            context_window=context_window,
            user_id=user_id,
        )

    async def set_session_source_csv(self, session_id: str, export_id: str | None, *, user_id: int | None = None):
        return await self._store.set_session_source_csv_async(session_id, export_id, user_id=user_id)

    async def seed_summary(self, session_id: str, summary_text: str):
        return await self._store.seed_summary_async(session_id, summary_text)

    async def get_session_list_entry(
        self,
        session_id: str,
        *,
        user_id: int | None = None,
    ) -> ConversationListEntry | None:
        rows = await self.list_sessions(user_id=user_id)
        return next((row for row in rows if row.id == session_id), None)


@dataclass(frozen=True)
class ExportRepository:
    _store: RuntimeStore

    async def list_exports(self, *, user_id: int | None = None):
        return await self._store.list_exports_async(user_id=user_id)

    async def get_export(self, export_id: str, *, user_id: int | None = None):
        return await self._store.get_export_async(export_id, user_id=user_id)

    async def get_export_by_filename(self, filename: str, *, user_id: int | None = None):
        return await self._store.get_export_by_filename_async(filename, user_id=user_id)

    async def update_export_title(self, export_id: str, title: str, *, user_id: int | None = None):
        return await self._store.update_export_title_async(export_id, title, user_id=user_id)

    async def delete_export(self, export_id: str, *, user_id: int | None = None):
        return await self._store.delete_export_async(export_id, user_id=user_id)


@dataclass(frozen=True)
class RepositoryBundle:
    users: UserRepository
    conversations: ConversationRepository
    exports: ExportRepository

    @classmethod
    def from_store(cls, store: RuntimeStore) -> "RepositoryBundle":
        return cls(
            users=UserRepository(store),
            conversations=ConversationRepository(store),
            exports=ExportRepository(store),
        )
