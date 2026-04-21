"""Domain-focused repository adapters over the sqlite runtime store.

What lives here now is the narrow slice of store access that actually
earns a class — row → domain-object mapping for session listings.
`UserRepository`, `ExportRepository`, and `RepositoryBundle` used to
live here as pure namespace shells over `RuntimeStore` and have been
removed: services + routes depend on `RuntimeStore` directly for those
operations now.

`ConversationRepository` survives because `list_sessions` and
`get_session_list_entry` do actual mapping from raw rows into
`ConversationListEntry` dataclasses — they're shaping, not
forwarding.
"""

from __future__ import annotations

from dataclasses import dataclass

from storage import RuntimeStore


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
class ConversationRepository:
    """Narrow mapping layer over `RuntimeStore.list_sessions_async` that
    shapes raw rows into `ConversationListEntry`. Everything else
    (`get_session`, `update_session`, `delete_session`, etc.) is plain
    store access — callers go through `RuntimeStore` directly."""

    _store: RuntimeStore

    async def list_sessions(self, *, user_id: int | None = None) -> list[ConversationListEntry]:
        rows = await self._store.list_sessions_async(user_id=user_id)
        return [ConversationListEntry.from_row(row) for row in rows]

    async def get_session_list_entry(
        self,
        session_id: str,
        *,
        user_id: int | None = None,
    ) -> ConversationListEntry | None:
        rows = await self.list_sessions(user_id=user_id)
        return next((row for row in rows if row.id == session_id), None)
