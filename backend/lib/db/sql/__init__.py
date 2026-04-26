"""SQL-touching code: SQLModel tables, RuntimeStore, per-domain query mixins.

Anything in this subtree imports SQLAlchemy or SQLModel. Code that doesn't
need SQL should import from `backend.lib.db.types` (or use `backend.lib.db`
re-exports) so the SQL boundary stays grep-checkable.
"""
