"""Sessions, turns, assistant parts, tool runs, compaction summaries,
and transcript assembly.

Mixed into `RuntimeStore` — expects `self._connect()` to yield a
`sqlite3.Connection` and `self._locks` to hold the session-level
asyncio.Lock dict (accessed by `delete_session`).
"""

from __future__ import annotations

import json

from storage._rows import (
    row_to_part,
    row_to_session,
    row_to_summary,
    row_to_tool_run,
    row_to_turn,
)
from storage.records import (
    AssistantPartRecord,
    CompactionSummaryRecord,
    SessionRecord,
    SessionTranscript,
    ToolRunRecord,
    TurnRecord,
    new_id,
    safe_load_tool_input,
    utcnow,
    wrap_summaries_for_prompt,
)


class TranscriptsMixin:
    """Session-level persistence: sessions, turns, parts, tool_runs, compaction summaries."""

    # ---------------- sessions ----------------

    def get_or_create_session(
        self,
        session_id: str | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
        context_window: int = 0,
        user_id: int | None = None,
    ) -> SessionRecord:
        existing = self.get_session(session_id, user_id=user_id) if session_id else None
        if existing:
            changed = False
            if provider and existing.provider != provider:
                existing.provider = provider
                changed = True
            if model and existing.model != model:
                existing.model = model
                changed = True
            if context_window and existing.context_window != context_window:
                existing.context_window = context_window
                changed = True
            if changed:
                self.update_session(existing)
            return existing

        now = utcnow()
        session = SessionRecord(
            id=session_id or new_id(),
            created_at=now,
            updated_at=now,
            provider=provider,
            model=model,
            context_window=context_window,
            user_id=user_id,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, created_at, updated_at, provider, model, title, context_window, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.created_at,
                    session.updated_at,
                    session.provider,
                    session.model,
                    session.title,
                    session.context_window,
                    session.user_id,
                ),
            )
        return session

    def update_session(self, session: SessionRecord) -> None:
        session.updated_at = utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET updated_at = ?, provider = ?, model = ?, title = ?,
                    context_window = ?, pinned_at = ?, source_csv_id = ?
                WHERE id = ?
                """,
                (
                    session.updated_at,
                    session.provider,
                    session.model,
                    session.title,
                    session.context_window,
                    session.pinned_at,
                    session.source_csv_id,
                    session.id,
                ),
            )

    def set_session_pinned(
        self, session_id: str, pinned: bool, *, user_id: int | None = None
    ) -> SessionRecord | None:
        """Pin or unpin a session. Pinning stamps pinned_at so callers can
        order most-recently-pinned first; unpinning clears it. Does not touch
        updated_at so pinning a stale conversation doesn't fake recency."""
        session = self.get_session(session_id, user_id=user_id)
        if session is None:
            return None
        session.pinned_at = utcnow() if pinned else None
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET pinned_at = ? WHERE id = ?",
                (session.pinned_at, session_id),
            )
        return session

    def set_session_source_csv(
        self, session_id: str, export_id: str | None, *, user_id: int | None = None
    ) -> SessionRecord | None:
        session = self.get_session(session_id, user_id=user_id)
        if session is None:
            return None
        session.source_csv_id = export_id
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET source_csv_id = ? WHERE id = ?",
                (export_id, session_id),
            )
        return session

    def seed_summary(self, session_id: str, summary_text: str) -> TurnRecord:
        """Insert a synthetic summary turn without recording a compaction event.

        Used to seed a fresh session with assistant-visible context (e.g. the
        SQL that produced an opened CSV). The turn is stored with role='summary'
        so `build_model_messages` prefixes it with '[Compacted summary]' and
        the LLM treats it as established context — no separate branch in the
        runtime loop. We skip the compaction_summaries row because nothing is
        being compacted; transcript.summaries stays empty for real compaction
        events only.
        """
        return self.create_turn(session_id, "summary", text=summary_text, status="completed")

    def get_session(self, session_id: str | None, *, user_id: int | None = None) -> SessionRecord | None:
        if not session_id:
            return None
        with self._connect() as conn:
            if user_id is not None:
                row = conn.execute(
                    "SELECT * FROM sessions WHERE id = ? AND user_id = ?",
                    (session_id, user_id),
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return row_to_session(row) if row else None

    # SELECT clause shared between list_sessions and get_session_list_row so
    # both return rows shaped identically for ConversationListEntry.from_row.
    _SESSION_LIST_SELECT = """
        s.id,
        s.updated_at,
        s.pinned_at,
        s.source_csv_id,
        COALESCE(s.title, (
            SELECT SUBSTR(text, 1, 60)
            FROM turns t
            WHERE t.session_id = s.id AND t.role = 'user'
            ORDER BY t.created_at
            LIMIT 1
        ), 'New conversation') AS title,
        s.provider,
        s.model,
        (SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id) AS turn_count
    """

    def list_sessions(self, *, user_id: int | None = None) -> list[dict]:
        sql = (
            f"SELECT {self._SESSION_LIST_SELECT} FROM sessions s "
            + ("WHERE s.user_id = ? " if user_id is not None else "")
            + "ORDER BY s.pinned_at DESC, s.updated_at DESC"
        )
        params: tuple = (user_id,) if user_id is not None else ()
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_session_list_row(
        self, session_id: str, *, user_id: int | None = None
    ) -> dict | None:
        """Single-row sibling of `list_sessions`: return the same projection
        shape for one session, or None if it doesn't exist (or isn't owned
        by the user when `user_id` is scoped).

        Exists so callers that already know a session id don't have to list
        every session and filter in Python — used by the conversations
        service after a mutation to re-read the updated row.
        """
        if user_id is not None:
            sql = f"SELECT {self._SESSION_LIST_SELECT} FROM sessions s WHERE s.id = ? AND s.user_id = ?"
            params: tuple = (session_id, user_id)
        else:
            sql = f"SELECT {self._SESSION_LIST_SELECT} FROM sessions s WHERE s.id = ?"
            params = (session_id,)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def delete_session(self, session_id: str, *, user_id: int | None = None) -> bool:
        with self._connect() as conn:
            if user_id is not None:
                row = conn.execute(
                    "SELECT id FROM sessions WHERE id = ? AND user_id = ?",
                    (session_id, user_id),
                ).fetchone()
            else:
                row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM assistant_parts WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM tool_runs WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM compaction_summaries WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return True

    # ---------------- turns ----------------

    def create_turn(self, session_id: str, role: str, text: str = "", status: str = "completed") -> TurnRecord:
        now = utcnow()
        turn = TurnRecord(
            id=new_id(),
            session_id=session_id,
            role=role,
            status=status,
            text=text,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO turns (id, session_id, role, status, text, compacted, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?)
                """,
                (turn.id, turn.session_id, turn.role, turn.status, turn.text, turn.created_at, turn.updated_at),
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        return turn

    def update_turn(self, turn_id: str, **changes) -> TurnRecord:
        if not changes:
            return self.get_turn(turn_id)
        fields = []
        values = []
        for key, value in changes.items():
            fields.append(f"{key} = ?")
            values.append(value)
        values.extend([utcnow(), turn_id])
        sql = f"UPDATE turns SET {', '.join(fields)}, updated_at = ? WHERE id = ?"
        with self._connect() as conn:
            conn.execute(sql, values)
        return self.get_turn(turn_id)

    def append_turn_text(self, turn_id: str, text: str) -> TurnRecord:
        turn = self.get_turn(turn_id)
        if turn is None:
            raise KeyError(f"Unknown turn {turn_id}")
        return self.update_turn(turn_id, text=turn.text + text)

    def append_assistant_text(self, session_id: str, turn_id: str, text: str) -> TurnRecord:
        """Append assistant text and persist its matching assistant_part atomically."""
        turn = self.get_turn(turn_id)
        if turn is None:
            raise KeyError(f"Unknown turn {turn_id}")
        now = utcnow()
        with self._connect() as conn:
            conn.execute(
                "UPDATE turns SET text = COALESCE(text, '') || ?, updated_at = ? WHERE id = ?",
                (text, now, turn_id),
            )
            row = conn.execute(
                "SELECT COALESCE(MAX(order_index), -1) + 1 FROM assistant_parts WHERE turn_id = ?",
                (turn_id,),
            ).fetchone()
            order_index = int(row[0]) if row else 0
            part = AssistantPartRecord(
                id=new_id(),
                session_id=session_id,
                turn_id=turn_id,
                kind="text",
                order_index=order_index,
                content=text,
            )
            conn.execute(
                """
                INSERT INTO assistant_parts (id, session_id, turn_id, kind, order_index, content, name, tool_run_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    part.id,
                    part.session_id,
                    part.turn_id,
                    part.kind,
                    part.order_index,
                    part.content,
                    part.name,
                    part.tool_run_id,
                    part.created_at,
                ),
            )
        return self.get_turn(turn_id)

    def get_turn(self, turn_id: str) -> TurnRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)).fetchone()
        return row_to_turn(row) if row else None

    # ---------------- assistant parts ----------------

    def add_part(
        self,
        session_id: str,
        turn_id: str,
        kind: str,
        content: str,
        *,
        name: str | None = None,
        tool_run_id: str | None = None,
    ) -> AssistantPartRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(order_index), -1) + 1 FROM assistant_parts WHERE turn_id = ?",
                (turn_id,),
            ).fetchone()
            order_index = int(row[0]) if row else 0
            part = AssistantPartRecord(
                id=new_id(),
                session_id=session_id,
                turn_id=turn_id,
                kind=kind,
                order_index=order_index,
                content=content,
                name=name,
                tool_run_id=tool_run_id,
            )
            conn.execute(
                """
                INSERT INTO assistant_parts (id, session_id, turn_id, kind, order_index, content, name, tool_run_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    part.id,
                    part.session_id,
                    part.turn_id,
                    part.kind,
                    part.order_index,
                    part.content,
                    part.name,
                    part.tool_run_id,
                    part.created_at,
                ),
            )
        return part

    # ---------------- tool runs ----------------

    def create_tool_run(
        self,
        session_id: str,
        turn_id: str,
        tool_name: str,
        input_data: dict,
        status: str = "pending",
        *,
        raw_input_text: str | None = None,
    ) -> ToolRunRecord:
        now = utcnow()
        tool_run = ToolRunRecord(
            id=new_id(),
            session_id=session_id,
            turn_id=turn_id,
            tool_name=tool_name,
            input_json=json.dumps(input_data, sort_keys=True),
            status=status,
            result_text=None,
            error_text=None,
            hint=None,
            duration_ms=None,
            compacted=False,
            created_at=now,
            updated_at=now,
            raw_input_text=raw_input_text,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tool_runs (
                    id, session_id, turn_id, tool_name, input_json, status,
                    result_text, error_text, hint, duration_ms, compacted,
                    created_at, updated_at, raw_input_text
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, 0, ?, ?, ?)
                """,
                (
                    tool_run.id,
                    tool_run.session_id,
                    tool_run.turn_id,
                    tool_run.tool_name,
                    tool_run.input_json,
                    tool_run.status,
                    tool_run.created_at,
                    tool_run.updated_at,
                    tool_run.raw_input_text,
                ),
            )
        return tool_run

    def update_tool_run(self, tool_run_id: str, **changes) -> ToolRunRecord:
        if not changes:
            return self.get_tool_run(tool_run_id)
        fields = []
        values = []
        for key, value in changes.items():
            fields.append(f"{key} = ?")
            values.append(value)
        values.extend([utcnow(), tool_run_id])
        sql = f"UPDATE tool_runs SET {', '.join(fields)}, updated_at = ? WHERE id = ?"
        with self._connect() as conn:
            conn.execute(sql, values)
        return self.get_tool_run(tool_run_id)

    def get_tool_run(self, tool_run_id: str) -> ToolRunRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tool_runs WHERE id = ?", (tool_run_id,)).fetchone()
        return row_to_tool_run(row) if row else None

    def get_recent_tool_runs(self, session_id: str, limit: int = 3) -> list[ToolRunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tool_runs
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [row_to_tool_run(r) for r in rows]

    # ---------------- compaction ----------------

    def record_compaction(self, session_id: str, summary_text: str, source_turn_ids: list[str]) -> CompactionSummaryRecord:
        now = utcnow()
        summary_turn = TurnRecord(
            id=new_id(),
            session_id=session_id,
            role="summary",
            status="completed",
            text=summary_text,
            created_at=now,
            updated_at=now,
        )
        summary = CompactionSummaryRecord(
            id=new_id(),
            session_id=session_id,
            summary_turn_id=summary_turn.id,
            source_turn_ids=source_turn_ids,
            created_at=now,
        )
        encoded = json.dumps(source_turn_ids)
        # All writes share one connection/transaction so a crash mid-compaction
        # can't leave an orphan summary turn without its compaction_summaries
        # row (or with source turns still marked active).
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO turns (id, session_id, role, status, text, compacted, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?)
                """,
                (
                    summary_turn.id, summary_turn.session_id, summary_turn.role,
                    summary_turn.status, summary_turn.text,
                    summary_turn.created_at, summary_turn.updated_at,
                ),
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            conn.execute(
                """
                INSERT INTO compaction_summaries (id, session_id, summary_turn_id, source_turn_ids, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (summary.id, session_id, summary.summary_turn_id, encoded, summary.created_at),
            )
            if source_turn_ids:
                placeholders = ", ".join("?" for _ in source_turn_ids)
                conn.execute(
                    f"UPDATE turns SET compacted = 1, updated_at = ? WHERE id IN ({placeholders})",
                    (now, *source_turn_ids),
                )
                conn.execute(
                    f"""
                    UPDATE tool_runs
                    SET compacted = 1, updated_at = ?
                    WHERE turn_id IN ({placeholders}) AND status = 'completed'
                    """,
                    (now, *source_turn_ids),
                )
        return summary

    # ---------------- transcript + message build ----------------

    def get_transcript(self, session_id: str) -> SessionTranscript:
        """Snapshot the full transcript: session + turns + parts + tool runs + summaries.

        Issues four separate SELECTs in a single connection but without an
        explicit `BEGIN`, so each statement is its own autocommit read. In
        WAL mode this is *read-consistent only when no concurrent writer
        commits between statements*. The runtime hot path guarantees that
        by holding `conversations.lock(session_id)` around any write, so
        compaction and message-building see a coherent snapshot. The HTTP
        transcript endpoint does *not* take the lock — if a turn is
        actively streaming when the user opens the session, the response
        can show a half-written assistant turn. That's treated as cosmetic:
        the next poll returns a consistent view. Do not loosen the lock
        contract for writers without wrapping this body in a deferred
        transaction.
        """
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(f"Unknown session {session_id}")
        with self._connect() as conn:
            turn_rows = conn.execute(
                "SELECT * FROM turns WHERE session_id = ? ORDER BY created_at, id",
                (session_id,),
            ).fetchall()
            part_rows = conn.execute(
                """
                SELECT * FROM assistant_parts
                WHERE session_id = ?
                ORDER BY turn_id, order_index, created_at
                """,
                (session_id,),
            ).fetchall()
            tool_rows = conn.execute(
                "SELECT * FROM tool_runs WHERE session_id = ? ORDER BY created_at, id",
                (session_id,),
            ).fetchall()
            summary_rows = conn.execute(
                "SELECT * FROM compaction_summaries WHERE session_id = ? ORDER BY created_at, id",
                (session_id,),
            ).fetchall()
        turns = [row_to_turn(r) for r in turn_rows]
        parts_by_turn: dict[str, list[AssistantPartRecord]] = {}
        for row in part_rows:
            part = row_to_part(row)
            parts_by_turn.setdefault(part.turn_id, []).append(part)
        tool_runs_by_turn: dict[str, list[ToolRunRecord]] = {}
        for row in tool_rows:
            tool_run = row_to_tool_run(row)
            tool_runs_by_turn.setdefault(tool_run.turn_id, []).append(tool_run)
        summaries = [row_to_summary(r) for r in summary_rows]
        return SessionTranscript(
            session=session,
            turns=turns,
            parts_by_turn=parts_by_turn,
            tool_runs_by_turn=tool_runs_by_turn,
            summaries=summaries,
        )
