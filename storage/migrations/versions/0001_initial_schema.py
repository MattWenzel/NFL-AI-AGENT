"""Initial schema baseline.

Creates every runtime table at the current post-migration shape. Uses
`metadata.create_all` rather than explicit `op.create_table(...)` calls so
pre-existing DBs (already populated by the legacy `init_db` path) bind
as a no-op — each `CREATE TABLE IF NOT EXISTS` short-circuits — and the
revision then stamps `alembic_version` at head.

Future schema changes land as new revisions with explicit `op.add_column`
etc.; this baseline only needs to cover the pre-Alembic state.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-04-21
"""

from typing import Sequence, Union

from alembic import op

from storage.models import SQLModel

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    SQLModel.metadata.create_all(bind)


def downgrade() -> None:
    bind = op.get_bind()
    SQLModel.metadata.drop_all(bind)
