"""Sessions, turns, assistant parts, tool runs, compaction summaries,
and transcript assembly.

Mixed into `RuntimeStore` — every method is async-native via the store's
`async_sessionmaker` (`self._async_session`).
"""

from __future__ import annotations

from sqlalchemy import delete, func, insert, literal, select, update

from storage.models import (
    AssistantPartRecord,
    CompactionSummaryRecord,
    ExportRecord,
    SessionRecord,
    SessionTranscript,
    ToolRunRecord,
    TurnRecord,
    new_id,
    utcnow,
)


# Projection emitted by `list_sessions` / `get_session_list_row` — both
# return list[dict] with the same keys so downstream code
# (`ConversationListEntry.from_row`) accepts either.
def _session_list_projection():
    """Build the SELECT columns for the session list projection.

    The `title` column COALESCEs the stored session.title with the first
    user turn's text (truncated to 60 chars), then "New conversation".
    `turn_count` is a correlated subquery.
    """
    first_user_turn = (
        select(TurnRecord.text)
        .where(
            TurnRecord.session_id == SessionRecord.id,
            TurnRecord.role == "user",
        )
        .order_by(TurnRecord.created_at)
        .limit(1)
        .correlate(SessionRecord)
        .scalar_subquery()
    )
    turn_count = (
        select(func.count())
        .select_from(TurnRecord)
        .where(TurnRecord.session_id == SessionRecord.id)
        .correlate(SessionRecord)
        .scalar_subquery()
    )
    return (
        SessionRecord.id,
        SessionRecord.updated_at,
        SessionRecord.pinned_at,
        SessionRecord.source_csv_id,
        func.coalesce(
            SessionRecord.title,
            func.substr(first_user_turn, 1, 60),
            "New conversation",
        ).label("title"),
        SessionRecord.provider,
        SessionRecord.model,
        turn_count.label("turn_count"),
    )


class TranscriptsMixin:
    """Async session-level persistence: sessions, turns, parts, tool_runs,
    compaction summaries."""

    # ---------------- sessions ----------------

    async def get_or_create_session(
        self,
        session_id: str | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
        context_window: int = 0,
        user_id: int | None = None,
    ) -> SessionRecord:
        existing = await self.get_session(session_id, user_id=user_id) if session_id else None
        if existing:
            changes: dict[str, object] = {}
            if provider and existing.provider != provider:
                existing.provider = provider
                changes["provider"] = provider
            if model and existing.model != model:
                existing.model = model
                changes["model"] = model
            if context_window and existing.context_window != context_window:
                existing.context_window = context_window
                changes["context_window"] = context_window
            if changes:
                await self.update_session(existing.id, **changes)
            return existing

        now = utcnow()
        record = SessionRecord(
            id=session_id or new_id(),
            created_at=now,
            updated_at=now,
            provider=provider,
            model=model,
            context_window=context_window,
            user_id=user_id,
        )
        async with self._async_session() as session:
            session.add(record)
            await session.commit()
        return record

    async def update_session(self, session_id: str, **changes) -> SessionRecord | None:
        if not changes:
            return await self.get_session(session_id)
        now = utcnow()
        async with self._async_session() as session:
            await session.execute(
                update(SessionRecord)
                .where(SessionRecord.id == session_id)
                .values(**changes, updated_at=now)
            )
            await session.commit()
        return await self.get_session(session_id)

    async def set_session_pinned(
        self, session_id: str, pinned: bool, *, user_id: int | None = None
    ) -> SessionRecord | None:
        """Pin or unpin a session. Pinning stamps pinned_at so callers can
        order most-recently-pinned first; unpinning clears it. Does not touch
        updated_at so pinning a stale conversation doesn't fake recency."""
        record = await self.get_session(session_id, user_id=user_id)
        if record is None:
            return None
        record.pinned_at = utcnow() if pinned else None
        async with self._async_session() as session:
            await session.execute(
                update(SessionRecord)
                .where(SessionRecord.id == session_id)
                .values(pinned_at=record.pinned_at)
            )
            await session.commit()
        return record

    async def set_session_source_csv(
        self, session_id: str, export_id: str | None, *, user_id: int | None = None
    ) -> SessionRecord | None:
        record = await self.get_session(session_id, user_id=user_id)
        if record is None:
            return None
        record.source_csv_id = export_id
        async with self._async_session() as session:
            await session.execute(
                update(SessionRecord)
                .where(SessionRecord.id == session_id)
                .values(source_csv_id=export_id)
            )
            await session.commit()
        return record

    async def seed_summary(self, session_id: str, summary_text: str) -> TurnRecord:
        """Insert a synthetic summary turn without recording a compaction event.

        Used to seed a fresh session with assistant-visible context (e.g. the
        SQL that produced an opened CSV). The turn is stored with role='summary'
        so `build_model_messages` prefixes it with '[Compacted summary]' and
        the LLM treats it as established context — no separate branch in the
        runtime loop. We skip the compaction_summaries row because nothing is
        being compacted; transcript.summaries stays empty for real compaction
        events only.
        """
        return await self.create_turn(session_id, "summary", text=summary_text, status="completed")

    async def get_session(
        self, session_id: str | None, *, user_id: int | None = None
    ) -> SessionRecord | None:
        if not session_id:
            return None
        async with self._async_session() as session:
            stmt = select(SessionRecord).where(SessionRecord.id == session_id)
            if user_id is not None:
                stmt = stmt.where(SessionRecord.user_id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_sessions(self, *, user_id: int | None = None) -> list[dict]:
        async with self._async_session() as session:
            stmt = select(*_session_list_projection())
            if user_id is not None:
                stmt = stmt.where(SessionRecord.user_id == user_id)
            stmt = stmt.order_by(
                SessionRecord.pinned_at.desc(), SessionRecord.updated_at.desc()
            )
            result = await session.execute(stmt)
            return [dict(row._mapping) for row in result.all()]

    async def get_session_list_row(
        self, session_id: str, *, user_id: int | None = None
    ) -> dict | None:
        """Single-row sibling of `list_sessions`: return the same projection
        shape for one session, or None if it doesn't exist (or isn't owned
        by the user when `user_id` is scoped)."""
        async with self._async_session() as session:
            stmt = select(*_session_list_projection()).where(SessionRecord.id == session_id)
            if user_id is not None:
                stmt = stmt.where(SessionRecord.user_id == user_id)
            result = await session.execute(stmt)
            row = result.first()
            return dict(row._mapping) if row else None

    async def delete_session(
        self, session_id: str, *, user_id: int | None = None
    ) -> bool:
        async with self._async_session() as session:
            stmt = select(SessionRecord.id).where(SessionRecord.id == session_id)
            if user_id is not None:
                stmt = stmt.where(SessionRecord.user_id == user_id)
            found = await session.execute(stmt)
            if found.scalar_one_or_none() is None:
                return False
            await session.execute(
                delete(AssistantPartRecord).where(AssistantPartRecord.session_id == session_id)
            )
            await session.execute(
                delete(ToolRunRecord).where(ToolRunRecord.session_id == session_id)
            )
            await session.execute(
                delete(CompactionSummaryRecord).where(
                    CompactionSummaryRecord.session_id == session_id
                )
            )
            await session.execute(
                delete(TurnRecord).where(TurnRecord.session_id == session_id)
            )
            await session.execute(
                delete(SessionRecord).where(SessionRecord.id == session_id)
            )
            await session.commit()
        self._release_lock(session_id)
        return True

    # ---------------- turns ----------------

    async def create_turn(
        self, session_id: str, role: str, text: str = "", status: str = "completed"
    ) -> TurnRecord:
        now = utcnow()
        turn = TurnRecord(
            id=new_id(),
            session_id=session_id,
            role=role,
            status=status,
            text=text,
            compacted=False,
            input_tokens=0,
            output_tokens=0,
            created_at=now,
            updated_at=now,
        )
        async with self._async_session() as session:
            session.add(turn)
            await session.execute(
                update(SessionRecord)
                .where(SessionRecord.id == session_id)
                .values(updated_at=now)
            )
            await session.commit()
        return turn

    async def update_turn(self, turn_id: str, **changes) -> TurnRecord | None:
        if not changes:
            return await self.get_turn(turn_id)
        async with self._async_session() as session:
            await session.execute(
                update(TurnRecord)
                .where(TurnRecord.id == turn_id)
                .values(**changes, updated_at=utcnow())
            )
            await session.commit()
        return await self.get_turn(turn_id)

    async def append_turn_text(self, turn_id: str, text_delta: str) -> TurnRecord | None:
        turn = await self.get_turn(turn_id)
        if turn is None:
            raise KeyError(f"Unknown turn {turn_id}")
        return await self.update_turn(turn_id, text=turn.text + text_delta)

    async def append_assistant_text(
        self, session_id: str, turn_id: str, text_delta: str
    ) -> TurnRecord | None:
        """Append assistant text and persist its matching assistant_part atomically."""
        turn = await self.get_turn(turn_id)
        if turn is None:
            raise KeyError(f"Unknown turn {turn_id}")
        now = utcnow()
        async with self._async_session() as session:
            await session.execute(
                update(TurnRecord)
                .where(TurnRecord.id == turn_id)
                .values(
                    text=func.coalesce(TurnRecord.text, "") + text_delta,
                    updated_at=now,
                )
            )
            await session.execute(
                self._assistant_part_insert_statement(
                    part_id=new_id(),
                    session_id=session_id,
                    turn_id=turn_id,
                    kind="text",
                    content=text_delta,
                    created_at=now,
                )
            )
            await session.commit()
        return await self.get_turn(turn_id)

    async def get_turn(self, turn_id: str) -> TurnRecord | None:
        async with self._async_session() as session:
            result = await session.execute(
                select(TurnRecord).where(TurnRecord.id == turn_id)
            )
            return result.scalar_one_or_none()

    # ---------------- assistant parts ----------------

    async def add_part(
        self,
        session_id: str,
        turn_id: str,
        kind: str,
        content: str,
        *,
        name: str | None = None,
        tool_run_id: str | None = None,
    ) -> AssistantPartRecord:
        part_id = new_id()
        created_at = utcnow()
        async with self._async_session() as session:
            await session.execute(
                self._assistant_part_insert_statement(
                    part_id=part_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    kind=kind,
                    content=content,
                    name=name,
                    tool_run_id=tool_run_id,
                    created_at=created_at,
                )
            )
            await session.commit()
            result = await session.execute(
                select(AssistantPartRecord).where(AssistantPartRecord.id == part_id)
            )
            return result.scalar_one()

    # ---------------- tool runs ----------------

    async def create_tool_run(
        self,
        session_id: str,
        turn_id: str,
        tool_name: str,
        input_data: dict,
        status: str = "pending",
    ) -> ToolRunRecord:
        now = utcnow()
        tool_run = ToolRunRecord(
            id=new_id(),
            session_id=session_id,
            turn_id=turn_id,
            tool_name=tool_name,
            input=dict(input_data),
            status=status,
            compacted=False,
            created_at=now,
            updated_at=now,
        )
        async with self._async_session() as session:
            session.add(tool_run)
            await session.commit()
        return tool_run

    async def update_tool_run(self, tool_run_id: str, **changes) -> ToolRunRecord | None:
        if not changes:
            return await self.get_tool_run(tool_run_id)
        async with self._async_session() as session:
            await session.execute(
                update(ToolRunRecord)
                .where(ToolRunRecord.id == tool_run_id)
                .values(**changes, updated_at=utcnow())
            )
            await session.commit()
        return await self.get_tool_run(tool_run_id)

    async def get_tool_run(self, tool_run_id: str) -> ToolRunRecord | None:
        async with self._async_session() as session:
            result = await session.execute(
                select(ToolRunRecord).where(ToolRunRecord.id == tool_run_id)
            )
            return result.scalar_one_or_none()

    async def get_recent_tool_runs(
        self, session_id: str, limit: int = 3
    ) -> list[ToolRunRecord]:
        async with self._async_session() as session:
            result = await session.execute(
                select(ToolRunRecord)
                .where(ToolRunRecord.session_id == session_id)
                .order_by(ToolRunRecord.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    # ---------------- compaction ----------------

    async def record_compaction(
        self, session_id: str, summary_text: str, source_turn_ids: list[str]
    ) -> CompactionSummaryRecord:
        now = utcnow()
        summary_turn = TurnRecord(
            id=new_id(),
            session_id=session_id,
            role="summary",
            status="completed",
            text=summary_text,
            compacted=False,
            input_tokens=0,
            output_tokens=0,
            created_at=now,
            updated_at=now,
        )
        summary = CompactionSummaryRecord(
            id=new_id(),
            session_id=session_id,
            summary_turn_id=summary_turn.id,
            source_turn_ids=list(source_turn_ids),
            created_at=now,
        )
        # All writes share one AsyncSession/transaction so a crash mid-compaction
        # can't leave an orphan summary turn without its compaction_summaries
        # row (or with source turns still marked active).
        async with self._async_session() as session:
            session.add(summary_turn)
            # Flush the summary turn so the `compaction_summaries.summary_turn_id`
            # FK resolves when the summary row is inserted in the same transaction.
            await session.flush()
            session.add(summary)
            await session.execute(
                update(SessionRecord)
                .where(SessionRecord.id == session_id)
                .values(updated_at=now)
            )
            if source_turn_ids:
                await session.execute(
                    update(TurnRecord)
                    .where(TurnRecord.id.in_(source_turn_ids))
                    .values(compacted=True, updated_at=now)
                )
                await session.execute(
                    update(ToolRunRecord)
                    .where(
                        ToolRunRecord.turn_id.in_(source_turn_ids),
                        ToolRunRecord.status == "completed",
                    )
                    .values(compacted=True, updated_at=now)
                )
            await session.commit()
        return summary

    # ---------------- transcript + message build ----------------

    async def get_transcript(self, session_id: str) -> SessionTranscript:
        """Snapshot the full transcript: session + turns + parts + tool runs + summaries.

        Issues four SELECTs in a single `AsyncSession`. SA's default
        transactional state makes these statements see a coherent snapshot
        within the session. The runtime hot path also holds
        `store.lock(session_id)` around any write, so compaction and
        message-building see a consistent view. The HTTP transcript endpoint
        does *not* take the lock — if a turn is actively streaming when the
        user opens the session, the response can show a half-written
        assistant turn. That's treated as cosmetic: the next poll returns
        a consistent view.
        """
        async with self._async_session() as session:
            session_row = await session.execute(
                select(SessionRecord).where(SessionRecord.id == session_id)
            )
            record = session_row.scalar_one_or_none()
            if record is None:
                raise KeyError(f"Unknown session {session_id}")
            turn_rows = await session.execute(
                select(TurnRecord)
                .where(TurnRecord.session_id == session_id)
                .order_by(TurnRecord.created_at, TurnRecord.id)
            )
            part_rows = await session.execute(
                select(AssistantPartRecord)
                .where(AssistantPartRecord.session_id == session_id)
                .order_by(
                    AssistantPartRecord.turn_id,
                    AssistantPartRecord.order_index,
                    AssistantPartRecord.created_at,
                )
            )
            tool_rows = await session.execute(
                select(ToolRunRecord)
                .where(ToolRunRecord.session_id == session_id)
                .order_by(ToolRunRecord.created_at, ToolRunRecord.id)
            )
            summary_rows = await session.execute(
                select(CompactionSummaryRecord)
                .where(CompactionSummaryRecord.session_id == session_id)
                .order_by(CompactionSummaryRecord.created_at, CompactionSummaryRecord.id)
            )
        turns = list(turn_rows.scalars().all())
        parts_by_turn: dict[str, list[AssistantPartRecord]] = {}
        for part in part_rows.scalars().all():
            parts_by_turn.setdefault(part.turn_id, []).append(part)
        tool_runs_by_turn: dict[str, list[ToolRunRecord]] = {}
        for run in tool_rows.scalars().all():
            tool_runs_by_turn.setdefault(run.turn_id, []).append(run)
        summaries = list(summary_rows.scalars().all())
        return SessionTranscript(
            session=record,
            turns=turns,
            parts_by_turn=parts_by_turn,
            tool_runs_by_turn=tool_runs_by_turn,
            summaries=summaries,
        )

    def _assistant_part_insert_statement(
        self,
        *,
        part_id: str,
        session_id: str,
        turn_id: str,
        kind: str,
        content: str,
        created_at: str,
        name: str | None = None,
        tool_run_id: str | None = None,
    ):
        next_order = func.coalesce(func.max(AssistantPartRecord.order_index), -1) + 1
        return insert(AssistantPartRecord).from_select(
            [
                AssistantPartRecord.id,
                AssistantPartRecord.session_id,
                AssistantPartRecord.turn_id,
                AssistantPartRecord.kind,
                AssistantPartRecord.order_index,
                AssistantPartRecord.content,
                AssistantPartRecord.name,
                AssistantPartRecord.tool_run_id,
                AssistantPartRecord.created_at,
            ],
            select(
                literal(part_id),
                literal(session_id),
                literal(turn_id),
                literal(kind),
                next_order,
                literal(content),
                literal(name),
                literal(tool_run_id),
                literal(created_at),
            ).where(AssistantPartRecord.turn_id == turn_id),
        )
