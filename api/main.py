"""FastAPI application factory for nflverse database."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import chat, exports
from agent.runtime import ChatRuntime
from agent.runtime_store import RuntimeStore
from config import DB_PATH, PBP_DB_PATH, RUNTIME_DB_PATH, format_file_size

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup validation and cleanup."""
    # Re-apply logging config in the worker process (reload=True spawns a fresh process)
    from agent.logger import setup_logging
    setup_logging(verbose=os.environ.get("NFLVERSE_VERBOSE") == "1")

    app.state.runtime_store = RuntimeStore(RUNTIME_DB_PATH)
    app.state.chat_runtime = ChatRuntime(app.state.runtime_store)

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
    from agent.providers import list_providers
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

    # Export cleanup
    removed = exports.cleanup_old_exports()
    if removed:
        logger.info("Cleaned up %d old export file(s)", removed)

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

    # CORS — the app runs locally; `"null"` covers chat.html opened via file://.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:8001",
            "http://127.0.0.1:8001",
            "null",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(chat.router)
    app.include_router(exports.router)

    @app.get("/health")
    def health_check():
        """Health check endpoint."""
        return {"status": "ok"}

    return app


app = create_app()
