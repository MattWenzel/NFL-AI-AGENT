"""Turn, assistant-part, tool-run, and transcript persistence for `RuntimeStore`."""

from __future__ import annotations

from sqlalchemy import func, insert, literal, select, update

from backend.core.persistence.models import (
    AssistantPartRecord,
    CompactionSummaryRecord,
    SessionRecord,
    SessionTranscript,
    ToolRunRecord,
    TurnRecord,
    new_id,
    utcnow,
)


class TranscriptStoreMixin:
    """Async persistence for turns, tool runs, and transcript snapshots."""

    async def seed_summary(self, session_id: str, summary_text: str) -> TurnRecord:
        return await self.create_turn(session_id, "summary", text=summary_text, status="completed")

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
            error=None,
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
            result = await session.execute(select(TurnRecord).where(TurnRecord.id == turn_id))
            return result.scalar_one_or_none()

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
        async with self._async_session() as session:
            session.add(summary_turn)
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

    async def get_transcript(self, session_id: str) -> SessionTranscript:
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
        values_query = select(
            literal(part_id),
            literal(session_id),
            literal(turn_id),
            literal(kind),
            next_order,
            literal(content),
            literal(name),
            literal(tool_run_id),
            literal(created_at),
        ).where(AssistantPartRecord.turn_id == turn_id)
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
            values_query,
        )
