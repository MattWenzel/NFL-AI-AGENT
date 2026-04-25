"""FastAPI application factory for nflverse database."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.middleware import SecurityHeadersMiddleware
from server.routes import auth, chat, codex_oauth, conversations, csv_downloads, csv_library, google_oauth, providers, settings
from config import ALLOW_NULL_ORIGIN, ALLOWED_ORIGINS
from server.startup import configure_runtime_state, log_environment_state, run_housekeeping, validate_encryption

# Project root plus the browser UI directory. The UI lives under `web/`
# so product code and static assets stay grouped instead of living at the
# repo root.
APP_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = APP_ROOT / "web"

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

    # Security headers first so they wrap every response (including CORS
    # preflights and error replies). Starlette composes middleware in reverse
    # of registration order, so the one added first runs outermost — which is
    # where we want security headers.
    app.add_middleware(SecurityHeadersMiddleware)

    # CORS — local defaults cover the dev setup; ALLOWED_ORIGINS env var
    # replaces them wholesale for hosted deploys. The "null" origin (how
    # Chrome labels file:// pages) is off by default; set ALLOW_NULL_ORIGIN=1
    # to allow it for local file:// testing.
    default_origins = ["http://localhost:8001", "http://127.0.0.1:8001"]
    if ALLOW_NULL_ORIGIN:
        default_origins.append("null")
    origins = ALLOWED_ORIGINS or default_origins
    # allow_credentials=True so same-origin fetch() picks up the new session
    # cookie. The frontend hits the same origin as the API, so this isn't a
    # cross-origin risk — browsers still enforce the allowlist.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
    )

    # Include routers
    app.include_router(auth.router)
    app.include_router(settings.router)
    app.include_router(codex_oauth.router)
    app.include_router(google_oauth.router)
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
    # take precedence; this mount only catches /static/* asset requests and
    # the bare root.
    app.mount(
        "/static",
        StaticFiles(directory=WEB_ROOT / "static"),
        name="static",
    )

    @app.get("/", include_in_schema=False)
    def serve_ui():
        return FileResponse(WEB_ROOT / "index.html")

    return app


app = create_app()
