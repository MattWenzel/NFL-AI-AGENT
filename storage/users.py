"""Users, auth sessions, and per-user API key storage.

Mixed into `RuntimeStore` — expects `self._connect()` to yield a
`sqlite3.Connection` with `row_factory = sqlite3.Row`.
"""

from __future__ import annotations

from storage._rows import row_to_api_key, row_to_auth_session, row_to_user
from storage.records import (
    AuthSessionRecord,
    UserApiKeyRecord,
    UserRecord,
    utcnow,
)


class UsersMixin:
    """CRUD for users, user_api_keys, and auth_sessions."""

    # ---------------- users ----------------

    def count_users(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"])

    def create_user(
        self,
        *,
        email: str,
        password_hash: str,
        role: str = "user",
        email_verified_at: str | None = None,
    ) -> UserRecord:
        now = utcnow()
        normalized = email.strip().lower()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO users (email, password_hash, created_at, updated_at, role, email_verified_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (normalized, password_hash, now, now, role, email_verified_at),
            )
            user_id = int(cur.lastrowid)
        return UserRecord(
            id=user_id,
            email=normalized,
            password_hash=password_hash,
            created_at=now,
            updated_at=now,
            role=role,
            email_verified_at=email_verified_at,
        )

    def get_user_by_email(self, email: str) -> UserRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
            ).fetchone()
        return row_to_user(row) if row else None

    def get_user_by_id(self, user_id: int) -> UserRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return row_to_user(row) if row else None

    def update_user_password(self, user_id: int, password_hash: str) -> None:
        now = utcnow()
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (password_hash, now, user_id),
            )

    def delete_user(self, user_id: int) -> list[str]:
        """Full cascade delete of a user and all owned data.

        Returns the list of CSV filenames that were registered to this user so
        the caller can unlink them from disk — the DB row is gone by then.

        Order matters: sessions first (each via `delete_session` to cascade
        turns / parts / tool_runs / compaction_summaries), then exports, then
        the user row. auth_sessions and user_api_keys cascade automatically
        via the FK ON DELETE CASCADE declared in the schema.
        """
        session_ids = [s["id"] for s in self.list_sessions(user_id=user_id)]
        for sid in session_ids:
            self.delete_session(sid, user_id=user_id)
        filenames = [e.filename for e in self.list_exports(user_id=user_id)]
        with self._connect() as conn:
            conn.execute("DELETE FROM exports WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return filenames

    def count_orphan_rows(self) -> dict[str, int]:
        """Rows with NULL user_id in user-scoped tables.

        Expected to be {0, 0} after first registration (backfill_orphan_ownership
        runs once). A non-zero result in multi-user mode means data is invisible
        to the scoped queries — surfaced as a startup warning.
        """
        with self._connect() as conn:
            sessions_null = int(conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE user_id IS NULL"
            ).fetchone()[0])
            exports_null = int(conn.execute(
                "SELECT COUNT(*) FROM exports WHERE user_id IS NULL"
            ).fetchone()[0])
        return {"sessions": sessions_null, "exports": exports_null}

    def ensure_admin_exists(self) -> int | None:
        """Promote the oldest user to admin if no admin exists.

        Safety net for DBs that predate the `role` column (pre-multi-user
        registrations default to 'user' via ALTER TABLE). Returns the promoted
        user's id, or None if the invariant already held / there are no users.
        """
        with self._connect() as conn:
            has_admin = conn.execute(
                "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
            ).fetchone()
            if has_admin is not None:
                return None
            row = conn.execute(
                "SELECT id FROM users ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            uid = int(row["id"])
            conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (uid,))
            return uid

    def backfill_orphan_ownership(self, user_id: int) -> tuple[int, int]:
        """Assign the given user_id to any sessions/exports that have none.

        Called once after first registration in single-user mode so pre-existing
        conversations/exports become owned by that user.
        """
        with self._connect() as conn:
            s = conn.execute(
                "UPDATE sessions SET user_id = ? WHERE user_id IS NULL", (user_id,)
            )
            e = conn.execute(
                "UPDATE exports SET user_id = ? WHERE user_id IS NULL", (user_id,)
            )
            return (s.rowcount, e.rowcount)

    # ---------------- user_api_keys ----------------

    def upsert_api_key(self, *, user_id: int, provider: str, encrypted_key: str) -> UserApiKeyRecord:
        now = utcnow()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM user_api_keys WHERE user_id = ? AND provider = ?",
                (user_id, provider),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO user_api_keys (user_id, provider, encrypted_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, provider) DO UPDATE SET
                    encrypted_key = excluded.encrypted_key,
                    updated_at = excluded.updated_at
                """,
                (user_id, provider, encrypted_key, created_at, now),
            )
        return UserApiKeyRecord(
            user_id=user_id,
            provider=provider,
            encrypted_key=encrypted_key,
            created_at=created_at,
            updated_at=now,
        )

    def delete_api_key(self, *, user_id: int, provider: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM user_api_keys WHERE user_id = ? AND provider = ?",
                (user_id, provider),
            )
            return cur.rowcount > 0

    def get_api_key(self, *, user_id: int, provider: str) -> UserApiKeyRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_api_keys WHERE user_id = ? AND provider = ?",
                (user_id, provider),
            ).fetchone()
        return row_to_api_key(row) if row else None

    def list_api_keys(self, user_id: int) -> list[UserApiKeyRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM user_api_keys WHERE user_id = ? ORDER BY provider", (user_id,)
            ).fetchall()
        return [row_to_api_key(r) for r in rows]

    def user_has_api_key(self, *, user_id: int, provider: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM user_api_keys WHERE user_id = ? AND provider = ?",
                (user_id, provider),
            ).fetchone()
        return row is not None

    # ---------------- auth_sessions ----------------

    def create_auth_session(
        self, *, token: str, user_id: int, expires_at: str
    ) -> AuthSessionRecord:
        now = utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO auth_sessions (token, user_id, created_at, expires_at, last_used_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (token, user_id, now, expires_at, now),
            )
        return AuthSessionRecord(
            token=token,
            user_id=user_id,
            created_at=now,
            expires_at=expires_at,
            last_used_at=now,
        )

    def get_auth_session(self, token: str) -> AuthSessionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM auth_sessions WHERE token = ?", (token,)
            ).fetchone()
        return row_to_auth_session(row) if row else None

    def touch_auth_session(self, token: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE auth_sessions SET last_used_at = ? WHERE token = ?",
                (utcnow(), token),
            )

    def delete_auth_session(self, token: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
            return cur.rowcount > 0

    def invalidate_other_auth_sessions(self, user_id: int, keep_token: str) -> int:
        """Revoke every auth session for this user except the one in use.

        Called after a password change so stolen tokens can't outlive the
        rotation. Returns the number of tokens killed.
        """
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM auth_sessions WHERE user_id = ? AND token != ?",
                (user_id, keep_token),
            )
            return cur.rowcount

    def purge_expired_auth_sessions(self) -> int:
        now = utcnow()
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM auth_sessions WHERE expires_at < ?", (now,)
            )
            return cur.rowcount
