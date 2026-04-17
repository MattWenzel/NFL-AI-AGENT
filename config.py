"""Database configuration and shared utilities."""

import os
from pathlib import Path


def load_dotenv():
    """Load .env file from project root if it exists."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())

# All four paths are env-overridable so a deploy (e.g. Fly.io) can point them
# at a mounted persistent volume (typically /data/...) while local dev keeps
# using the repo-relative defaults.
_PROJECT_ROOT = Path(__file__).parent
DB_PATH = Path(os.environ.get("DB_PATH") or _PROJECT_ROOT / "NFLVERSE" / "data" / "nflverse.db")
PBP_DB_PATH = Path(os.environ.get("PBP_DB_PATH") or _PROJECT_ROOT / "NFLVERSE" / "data" / "pbp.db")

# Conversation persistence
RUNTIME_DB_PATH = Path(os.environ.get("RUNTIME_DB_PATH") or _PROJECT_ROOT / "data" / "runtime.sqlite3")

# CSV exports
EXPORTS_DIR = Path(os.environ.get("EXPORTS_DIR") or _PROJECT_ROOT / "exports")

# Auth / settings
AUTH_TOKEN_TTL_DAYS = int(os.environ.get("AUTH_TOKEN_TTL_DAYS", "30"))

# Optional invite code gate on /auth/register. If unset, registration is open
# (fine for local dev). Set to any random string to restrict signups to people
# you've shared it with — simple way to avoid opening a public app to the
# whole internet without a full invite-management system.
REGISTRATION_INVITE_CODE: str | None = os.environ.get("REGISTRATION_INVITE_CODE") or None

# CORS. Comma-separated origins; if unset we fall back to the localhost
# defaults baked into api/main.py (plus "null" for file:// dev). When
# deploying behind a real domain, set this to the prod origin(s).
_origins_env = os.environ.get("ALLOWED_ORIGINS", "").strip()
ALLOWED_ORIGINS: list[str] | None = (
    [o.strip() for o in _origins_env.split(",") if o.strip()]
    if _origins_env
    else None
)


def format_file_size(size_bytes: int) -> str:
    """Human-readable file size (B, KB, MB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"
