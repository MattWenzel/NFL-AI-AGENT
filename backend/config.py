"""Database configuration and shared utilities."""

import os
from pathlib import Path

_DOTENV_LOADED = False


def load_dotenv() -> None:
    """Load .env file from project root if it exists."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())
    _DOTENV_LOADED = True


load_dotenv()

# All four paths are env-overridable so a deploy (e.g. Fly.io) can point them
# at a mounted persistent volume (typically /data/...) while local dev keeps
# using the repo-relative defaults.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("DB_PATH") or _PROJECT_ROOT / "NFLVERSE" / "data" / "nflverse.duckdb")

# Conversation persistence
RUNTIME_DB_PATH = Path(os.environ.get("RUNTIME_DB_PATH") or _PROJECT_ROOT / "data" / "runtime.sqlite3")

# CSV exports
EXPORTS_DIR = Path(os.environ.get("EXPORTS_DIR") or _PROJECT_ROOT / "exports")

# Auth / settings
AUTH_TOKEN_TTL_DAYS = int(os.environ.get("AUTH_TOKEN_TTL_DAYS", "30"))
AUTH_SESSION_TOUCH_INTERVAL_SECONDS = int(
    os.environ.get("AUTH_SESSION_TOUCH_INTERVAL_SECONDS", "300")
)

# Optional invite code gate on /auth/register. If unset, registration is open
# (fine for local dev). Set to any random string to restrict signups to people
# you've shared it with — simple way to avoid opening a public app to the
# whole internet without a full invite-management system.
REGISTRATION_INVITE_CODE: str | None = os.environ.get("REGISTRATION_INVITE_CODE") or None

# Per-email login lockout. Layered on top of the per-IP rate limiter in
# backend/server/rate_limit.py — the IP limit stops one address pounding login,
# the per-email lockout stops an IP-rotating attacker targeting one account.
LOGIN_LOCKOUT_MAX_FAILURES = int(os.environ.get("LOGIN_LOCKOUT_MAX_FAILURES", "10"))
LOGIN_LOCKOUT_WINDOW_SECONDS = int(os.environ.get("LOGIN_LOCKOUT_WINDOW_SECONDS", "900"))
LOGIN_LOCKOUT_DURATION_SECONDS = int(os.environ.get("LOGIN_LOCKOUT_DURATION_SECONDS", "900"))

# Max concurrent /chat/stream connections per user. The chat runtime is
# expensive (LLM + tool execution); this guards against a single account
# hogging the server with parallel streams.
CHAT_STREAM_MAX_PER_USER = int(os.environ.get("CHAT_STREAM_MAX_PER_USER", "3"))

# Who may fall back to the server's env-var LLM keys (ANTHROPIC_API_KEY /
# OPENAI_API_KEY) when they haven't stored their own key in Settings:
#   "admin" (default) — only role='admin' users (the first registrant);
#   "all"             — every signed-in user spends on the server's keys;
#   "none"            — nobody; every user must bring their own key.
SHARED_PROVIDER_KEYS = os.environ.get("SHARED_PROVIDER_KEYS", "admin").strip().lower()

# Per-user request-rate caps (sliding 15-minute window). Users bring their
# own LLM keys, so these don't meter their token spend — they keep a
# scripted client from pegging this machine's CPU (each chat turn can run
# up to 10 sandbox SQL queries) and bandwidth. Generous on purpose; layered
# under the concurrency cap above, which bounds parallelism.
CHAT_REQUESTS_PER_QUARTER_HOUR = int(os.environ.get("CHAT_REQUESTS_PER_QUARTER_HOUR", "150"))
SQL_REQUESTS_PER_QUARTER_HOUR = int(os.environ.get("SQL_REQUESTS_PER_QUARTER_HOUR", "300"))

# Cap on rows in the CSV export library per user — bounds disk growth from
# create_csv_export / save-to-reports. Delete old exports to free slots.
MAX_EXPORTS_PER_USER = int(os.environ.get("MAX_EXPORTS_PER_USER", "200"))

# Email verification (Resend).
RESEND_API_KEY: str | None = os.environ.get("RESEND_API_KEY") or None
EMAIL_FROM_ADDRESS: str | None = os.environ.get("EMAIL_FROM_ADDRESS") or None
APP_BASE_URL: str = os.environ.get("APP_BASE_URL", "http://localhost:8001").rstrip("/")
# When "1", /auth/login rejects accounts that haven't clicked the verification
# link. Default off so existing users and dev setups aren't locked out; flip
# to "1" in prod once Resend DNS is verified.
EMAIL_VERIFICATION_REQUIRED = os.environ.get("EMAIL_VERIFICATION_REQUIRED", "0") == "1"

# Google OAuth. Both vars must be set for the /auth/oauth/google/* routes
# and the "Continue with Google" button to appear. Redirect URI is derived
# from APP_BASE_URL so you don't configure it twice.
GOOGLE_OAUTH_CLIENT_ID: str | None = os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or None
GOOGLE_OAUTH_CLIENT_SECRET: str | None = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or None


def google_oauth_enabled() -> bool:
    return bool(GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET)


def google_oauth_redirect_uri() -> str:
    return f"{APP_BASE_URL}/auth/oauth/google/callback"

# CORS. Comma-separated origins; if unset we fall back to the localhost
# defaults baked into backend/server/app.py. When deploying behind a real domain,
# set this to the prod origin(s).
_origins_env = os.environ.get("ALLOWED_ORIGINS", "").strip()
ALLOWED_ORIGINS: list[str] | None = (
    [o.strip() for o in _origins_env.split(",") if o.strip()]
    if _origins_env
    else None
)
# Dev-only escape hatch: set to "1" to allow the "null" origin (file:// pages).
# Off by default so a deploy can't accidentally accept null-origin requests.
ALLOW_NULL_ORIGIN = os.environ.get("ALLOW_NULL_ORIGIN", "0") == "1"


def format_file_size(size_bytes: int) -> str:
    """Human-readable file size (B, KB, MB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"
