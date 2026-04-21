"""Domain-focused repository adapters over the sqlite runtime store."""

from __future__ import annotations

from dataclasses import dataclass

from storage import RuntimeStore


@dataclass(frozen=True)
class UserRepository:
    store: RuntimeStore

    async def count_users(self) -> int:
        return await self.store.count_users_async()

    async def create_user(self, **kwargs):
        return await self.store.create_user_async(**kwargs)

    async def get_user_by_email(self, email: str):
        return await self.store.get_user_by_email_async(email)

    async def get_user_by_id(self, user_id: int):
        return await self.store.get_user_by_id_async(user_id)

    async def update_user_password(self, user_id: int, password_hash: str) -> None:
        await self.store.update_user_password_async(user_id, password_hash)

    async def delete_user(self, user_id: int) -> list[str]:
        return await self.store.delete_user_async(user_id)

    async def backfill_orphan_ownership(self, user_id: int) -> tuple[int, int]:
        return await self.store.backfill_orphan_ownership_async(user_id)

    async def ensure_admin_exists(self):
        return await self.store.ensure_admin_exists_async()

    async def count_orphan_rows(self) -> dict[str, int]:
        return await self.store.count_orphan_rows_async()

    async def purge_expired_auth_sessions(self) -> int:
        return await self.store.purge_expired_auth_sessions_async()

    async def upsert_api_key(self, **kwargs):
        return await self.store.upsert_api_key_async(**kwargs)

    async def delete_api_key(self, *, user_id: int, provider: str) -> bool:
        return await self.store.delete_api_key_async(user_id=user_id, provider=provider)

    async def get_api_key(self, *, user_id: int, provider: str):
        return await self.store.get_api_key_async(user_id=user_id, provider=provider)

    async def list_api_keys(self, user_id: int):
        return await self.store.list_api_keys_async(user_id)

    async def user_has_api_key(self, *, user_id: int, provider: str) -> bool:
        return await self.store.user_has_api_key_async(user_id=user_id, provider=provider)

    async def create_auth_session(self, **kwargs):
        return await self.store.create_auth_session_async(**kwargs)

    async def get_auth_session(self, token: str):
        return await self.store.get_auth_session_async(token)

    async def touch_auth_session(self, token: str, **kwargs):
        return await self.store.touch_auth_session_async(token, **kwargs)

    async def delete_auth_session(self, token: str) -> bool:
        return await self.store.delete_auth_session_async(token)

    async def invalidate_other_auth_sessions(self, user_id: int, *, keep_token: str) -> int:
        return await self.store.invalidate_other_auth_sessions_async(user_id, keep_token=keep_token)


@dataclass(frozen=True)
class ConversationRepository:
    store: RuntimeStore

    async def get_session(self, session_id: str | None, *, user_id: int | None = None):
        return await self.store.get_session_async(session_id, user_id=user_id)

    async def list_sessions(self, *, user_id: int | None = None):
        return await self.store.list_sessions_async(user_id=user_id)

    async def get_transcript(self, session_id: str):
        return await self.store.get_transcript_async(session_id)

    async def update_session(self, session) -> None:
        await self.store.update_session_async(session)

    async def set_session_pinned(self, session_id: str, pinned: bool, *, user_id: int | None = None):
        return await self.store.set_session_pinned_async(session_id, pinned, user_id=user_id)

    async def delete_session(self, session_id: str, *, user_id: int | None = None) -> bool:
        return await self.store.delete_session_async(session_id, user_id=user_id)

    async def get_or_create_session(self, **kwargs):
        return await self.store.get_or_create_session_async(**kwargs)

    async def set_session_source_csv(self, session_id: str, export_id: str | None, *, user_id: int | None = None):
        return await self.store.set_session_source_csv_async(session_id, export_id, user_id=user_id)

    async def seed_summary(self, session_id: str, summary_text: str):
        return await self.store.seed_summary_async(session_id, summary_text)


@dataclass(frozen=True)
class ExportRepository:
    store: RuntimeStore

    async def list_exports(self, *, user_id: int | None = None):
        return await self.store.list_exports_async(user_id=user_id)

    async def get_export(self, export_id: str, *, user_id: int | None = None):
        return await self.store.get_export_async(export_id, user_id=user_id)

    async def get_export_by_filename(self, filename: str, *, user_id: int | None = None):
        return await self.store.get_export_by_filename_async(filename, user_id=user_id)

    async def update_export_title(self, export_id: str, title: str, *, user_id: int | None = None):
        return await self.store.update_export_title_async(export_id, title, user_id=user_id)

    async def delete_export(self, export_id: str, *, user_id: int | None = None):
        return await self.store.delete_export_async(export_id, user_id=user_id)


@dataclass(frozen=True)
class RepositoryBundle:
    store: RuntimeStore
    users: UserRepository
    conversations: ConversationRepository
    exports: ExportRepository

    @classmethod
    def from_store(cls, store: RuntimeStore) -> "RepositoryBundle":
        return cls(
            store=store,
            users=UserRepository(store),
            conversations=ConversationRepository(store),
            exports=ExportRepository(store),
        )
