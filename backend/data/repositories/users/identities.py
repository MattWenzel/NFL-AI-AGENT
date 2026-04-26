"""user_identities CRUD: password / google / future OAuth providers."""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from backend.data.types.errors import IdentityConflictError
from backend.data.models import UserIdentityRecord, UserRecord, new_id, utcnow


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
