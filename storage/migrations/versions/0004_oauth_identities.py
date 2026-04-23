"""OAuth + password identity registry.

Adds `user_identities` so a user can have multiple auth methods (password,
Google OAuth, future Apple/GitHub/etc.) all pointing at the same `users`
row. Paired with the sentinel password_hash `"!"` that OAuth-only accounts
get, this lets auth methods be added or removed from an account without
a `users` table rebuild.

Uses the "inspect first, create if missing" pattern from 0002/0003 so a
fresh DB built via 0001's `metadata.create_all` (which now includes this
table via the new SQLModel class) doesn't double-create, while legacy DBs
stamped at 0003 get the new table.

Revision ID: 0004_oauth_identities
Revises: 0003_security_hardening
Create Date: 2026-04-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0004_oauth_identities"
down_revision: Union[str, None] = "0003_security_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "user_identities" in _existing_tables():
        return
    op.create_table(
        "user_identities",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("provider_subject", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.UniqueConstraint(
            "provider", "provider_subject", name="uq_user_identities_provider_subject"
        ),
    )
    op.create_index(
        "idx_user_identities_user",
        "user_identities",
        ["user_id"],
    )


def downgrade() -> None:
    if "user_identities" not in _existing_tables():
        return
    op.drop_index("idx_user_identities_user", table_name="user_identities")
    op.drop_table("user_identities")
