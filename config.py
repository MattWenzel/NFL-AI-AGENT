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

DB_PATH = Path(__file__).parent / "NFLVERSE" / "data" / "nflverse.db"
PBP_DB_PATH = Path(__file__).parent / "NFLVERSE" / "data" / "pbp.db"

# Conversation persistence
RUNTIME_DB_PATH = Path(__file__).parent / "data" / "runtime.sqlite3"

# CSV exports
EXPORTS_DIR = Path(__file__).parent / "exports"

# Auth / settings
AUTH_TOKEN_TTL_DAYS = int(os.environ.get("AUTH_TOKEN_TTL_DAYS", "30"))

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
