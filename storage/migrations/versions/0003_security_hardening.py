"""Security hardening: email verification, per-email login lockout, audit log.

Three new tables supporting the 2026-04-23 security review follow-up:

- `email_verifications` — one-shot signup/reset tokens consumed by
  /auth/verify-email. FK-cascades with users so account delete cleans up.
- `login_failures` — per-email failure counter and optional lockout. Layered
  on top of the per-IP rate limiter so an IP-rotating attacker targeting
  one account still hits a cap.
- `security_events` — audit trail of sensitive actions (login, key change,
  password change, etc.). `user_id` is nullable + ON DELETE SET NULL so
  deleted accounts leave an auditable footprint.

Uses the "inspect first, create if missing" pattern from 0002 so a fresh
DB built via 0001's `metadata.create_all` (which will already include these
tables from storage/models.py) doesn't double-create, while legacy DBs
stamped at 0002 get the new tables.

Revision ID: 0003_security_hardening
Revises: 0002_rename_toolrun_columns
Create Date: 2026-04-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0003_security_hardening"
down_revision: Union[str, None] = "0002_rename_toolrun_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _existing_tables()

    if "email_verifications" not in existing:
        op.create_table(
            "email_verifications",
            sa.Column("token", sa.String(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("purpose", sa.String(), nullable=False, server_default="signup"),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("expires_at", sa.String(), nullable=False),
            sa.Column("used_at", sa.String(), nullable=True),
        )
        op.create_index(
            "idx_email_verifications_user",
            "email_verifications",
            ["user_id"],
        )

    if "login_failures" not in existing:
        op.create_table(
            "login_failures",
            sa.Column("email", sa.String(), primary_key=True),
            sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_failure_at", sa.String(), nullable=False),
            sa.Column("locked_until", sa.String(), nullable=True),
        )

    if "security_events" not in existing:
        op.create_table(
            "security_events",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("ip", sa.String(), nullable=True),
            sa.Column("user_agent", sa.String(), nullable=True),
            sa.Column("metadata_json", sa.String(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.String(), nullable=False),
        )
        op.create_index(
            "idx_security_events_user_created",
            "security_events",
            ["user_id", sa.text("created_at DESC")],
        )
        op.create_index(
            "idx_security_events_created",
            "security_events",
            [sa.text("created_at DESC")],
        )


def downgrade() -> None:
    existing = _existing_tables()
    if "security_events" in existing:
        op.drop_index("idx_security_events_created", table_name="security_events")
        op.drop_index("idx_security_events_user_created", table_name="security_events")
        op.drop_table("security_events")
    if "login_failures" in existing:
        op.drop_table("login_failures")
    if "email_verifications" in existing:
        op.drop_index("idx_email_verifications_user", table_name="email_verifications")
        op.drop_table("email_verifications")
