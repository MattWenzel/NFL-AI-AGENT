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
from config import ALLOWED_ORIGINS
from server.startup import configure_runtime_state, log_environment_state, run_housekeeping, validate_encryption

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

    validate_encryption()
    configure_runtime_state(app)
    await run_housekeeping(app)
    log_environment_state()

    try:
        yield
    finally:
        process_state = getattr(app.state, "process_state", None)
        if process_state is not None:
            await process_state.aclose()


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
