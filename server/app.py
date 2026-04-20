"""FastAPI application factory for nflverse database."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.routes import auth, chat, codex_oauth, conversations, csv_downloads, csv_library, providers, settings
from agent.runtime import ChatRuntime
from auth import encryption
from storage import RuntimeStore
from config import ALLOWED_ORIGINS, DB_PATH, PBP_DB_PATH, RUNTIME_DB_PATH, format_file_size

# Project root — one level up from this file (server/app.py → project/).
# Used to resolve the UI's static assets and the chat.html entry point so the
# same process serves both the API and the front-end (single-origin deploy).
APP_ROOT = Path(__file__).resolve().parent.parent

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup validation and cleanup."""
    # Re-apply logging config in the worker process (reload=True spawns a fresh process)
    from server.logging import setup_logging
    setup_logging(verbose=os.environ.get("NFLVERSE_VERBOSE") == "1")

    # Encryption key must be present before we accept auth/settings traffic.
    # Fail fast at startup with a concrete hint rather than at the first PUT /settings/api-keys.
    try:
        encryption.require_configured()
    except encryption.EncryptionKeyMissing as exc:
        logger.error("%s", exc)
        raise
    except encryption.EncryptionKeyInvalid as exc:
        logger.error("%s", exc)
        raise

    app.state.runtime_store = RuntimeStore(RUNTIME_DB_PATH)
    app.state.chat_runtime = ChatRuntime(app.state.runtime_store)

    # Housekeeping: drop any long-expired auth sessions so the table doesn't grow forever.
    purged = app.state.runtime_store.purge_expired_auth_sessions()
    if purged:
        logger.info("Purged %d expired auth session(s)", purged)

    user_count = app.state.runtime_store.count_users()
    if user_count == 0:
        logger.info("No users registered yet — first visitor to the UI will be prompted to create an account.")
    else:
        logger.info("%d user account(s) registered", user_count)

        # Ensure an admin exists (safety net for DBs that predate the `role` column).
        promoted = app.state.runtime_store.ensure_admin_exists()
        if promoted is not None:
            logger.info("Promoted user %d to admin (no admin existed yet)", promoted)

        # Orphan-row check: after multi-user is live, any NULL user_id rows are
        # invisible to scoped queries and therefore inaccessible from the UI.
        # Should always be {0, 0} in normal operation.
        orphans = app.state.runtime_store.count_orphan_rows()
        if any(orphans.values()):
            logger.warning(
                "Found orphan rows with NULL user_id — invisible to scoped queries: %s",
                orphans,
            )

    # DB checks
    if DB_PATH.exists():
        size = format_file_size(DB_PATH.stat().st_size)
        logger.info("nflverse.db: %s (%s)", DB_PATH, size)
    else:
        logger.warning("nflverse.db NOT FOUND at %s — API endpoints will fail", DB_PATH)

    if PBP_DB_PATH.exists():
        size = format_file_size(PBP_DB_PATH.stat().st_size)
        logger.info("pbp.db: %s (%s)", PBP_DB_PATH, size)
    else:
        logger.warning("pbp.db not found at %s — PBP queries will fail", PBP_DB_PATH)

    # LLM provider checks
    from provider import list_providers
    configured = []
    for info in list_providers():
        api_key = os.environ.get(info.env_key, "")
        if api_key:
            masked = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
            logger.info("%s (%s): configured (%s)", info.display_name, info.env_key, masked)
            configured.append(info.name)
        else:
            logger.info("%s (%s): not configured", info.display_name, info.env_key)
    if not configured:
        logger.warning("No LLM providers configured — chat endpoints will return 503")

    if RUNTIME_DB_PATH.exists():
        size = format_file_size(RUNTIME_DB_PATH.stat().st_size)
        logger.info("runtime db: %s (%s)", RUNTIME_DB_PATH, size)

    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="nflverse API",
        description="NFL stats API with AI chat agent, schema discovery, and CSV export (1999-2025)",
        version="3.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS — local defaults cover the dev setup; ALLOWED_ORIGINS env var
    # replaces them wholesale for hosted deploys. `"null"` is how Chrome
    # reports file:// origins when chat.html is opened directly.
    origins = ALLOWED_ORIGINS or [
        "http://localhost:8001",
        "http://127.0.0.1:8001",
        "null",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(auth.router)
    app.include_router(settings.router)
    app.include_router(codex_oauth.router)
    app.include_router(chat.router)
    app.include_router(conversations.router)
    app.include_router(providers.router)
    app.include_router(csv_downloads.router)
    app.include_router(csv_library.router)

    @app.get("/health")
    def health_check():
        """Health check endpoint."""
        return {"status": "ok"}

    # Serve the UI from the same origin as the API. Specific API routes above
    # take precedence; this mount only catches /chat-ui/* asset requests and
    # the bare root. chat.html's <link>/<script> tags use relative paths
    # (chat-ui/...), so mounting at /chat-ui/ keeps those resolving.
    app.mount(
        "/chat-ui",
        StaticFiles(directory=APP_ROOT / "chat-ui"),
        name="chat-ui",
    )

    @app.get("/", include_in_schema=False)
    def serve_ui():
        return FileResponse(APP_ROOT / "chat.html")

    return app


app = create_app()
