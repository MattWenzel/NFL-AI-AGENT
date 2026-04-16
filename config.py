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

# OAuth token storage (Codex provider)
CODEX_AUTH_PATH = Path(__file__).parent / "data" / "codex_auth.json"

# CSV exports
EXPORTS_DIR = Path(__file__).parent / "exports"


def format_file_size(size_bytes: int) -> str:
    """Human-readable file size (B, KB, MB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"
