"""Rename tool_runs columns so the DB matches the Python attribute names.

`input_json` → `input`, `result_text` → `result`, `error_text` → `error`.
These were preserved verbatim in the SQLModel migration so pre-existing
`runtime.sqlite3` files bound cleanly without a schema change, at the
cost of a permanent Python/DB name split kept alive via `sa_column`
overrides. Collapsing them removes that mapping and the accompanying
mental tax in models, docs, and sqlite-CLI debugging.

Conditional on the old columns actually being present: revision 0001
uses `metadata.create_all`, which means a fresh DB built after this
rename already has the new column names (the models reflect the
post-rename shape). In that case there's nothing to rename and this
revision no-ops. Only legacy DBs stamped at 0001 before this landed
will see the alter.

`batch_alter_table` is Alembic's SQLite-safe wrapper: on SQLite 3.25+
it issues a native RENAME COLUMN; on older versions it falls back to
the copy-through-temp-table dance automatically. Either way, runs in
a single transaction so a mid-migration failure rolls back cleanly.

Revision ID: 0002_rename_toolrun_columns
Revises: 0001_initial_schema
Create Date: 2026-04-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0002_rename_toolrun_columns"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_LEGACY_TO_NEW = {
    "input_json": "input",
    "result_text": "result",
    "error_text": "error",
}


def _existing_columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("tool_runs")}


def upgrade() -> None:
    cols = _existing_columns()
    pending = {old: new for old, new in _LEGACY_TO_NEW.items() if old in cols}
    if not pending:
        return
    with op.batch_alter_table("tool_runs") as batch:
        for old, new in pending.items():
            batch.alter_column(old, new_column_name=new)


def downgrade() -> None:
    cols = _existing_columns()
    pending = {new: old for old, new in _LEGACY_TO_NEW.items() if new in cols}
    if not pending:
        return
    with op.batch_alter_table("tool_runs") as batch:
        for new, old in pending.items():
            batch.alter_column(new, new_column_name=old)
