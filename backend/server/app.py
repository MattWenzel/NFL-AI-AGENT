"""FastAPI application factory for nflverse database."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.config import ALLOW_NULL_ORIGIN, ALLOWED_ORIGINS
from backend.server.middleware import RequestIDMiddleware, SecurityHeadersMiddleware
from backend.server.startup import configure_runtime_state, log_environment_state, run_housekeeping, validate_encryption
from backend.api.routes import auth
from backend.api.routes import chat
from backend.api.routes import conversations
from backend.api.routes import exports
from backend.api.routes import oauth_google
from backend.api.routes import oauth_openai
from backend.api.routes import providers
from backend.api.routes import settings
from backend.api.routes import tables

# Project root plus the browser frontend directories. The Vite/React build
# (frontend-next/dist) is preferred when present — that's what the Docker
# image ships and what the production deploy serves. Falls back to the
# legacy vanilla-JS frontend/ tree when the dist isn't present (e.g. running
# `python3 run.py` locally without `npm run build`).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_NEXT_DIST = PROJECT_ROOT / "frontend-next" / "dist"
FRONTEND_LEGACY = PROJECT_ROOT / "frontend"

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup validation and cleanup."""
    # Re-apply logging config in the worker process (reload=True spawns a fresh process)
    from backend.server.logging import setup_logging
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

    # RequestIDMiddleware first so the correlation contextvar is set before
    # any other middleware runs (or logs). Starlette composes middleware in
    # reverse of registration order, so first-added = outermost.
    app.add_middleware(RequestIDMiddleware)

    # Security headers wrap every response (including CORS preflights and
    # error replies).
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
    app.include_router(oauth_google.router)
    app.include_router(oauth_openai.router)
    app.include_router(chat.router)
    app.include_router(conversations.router)
    app.include_router(tables.router)
    app.include_router(providers.router)
    app.include_router(exports.download_router)
    app.include_router(exports.router)

    @app.get("/health")
    def health_check():
        """Health check endpoint."""
        return {"status": "ok"}

    # Serve the UI from the same origin as the API. Specific API routes above
    # take precedence; the mounts below only catch asset requests and the
    # bare root. Prefer the Vite/React build; fall back to the legacy tree.
    if (FRONTEND_NEXT_DIST / "index.html").is_file():
        app.mount(
            "/assets",
            StaticFiles(directory=FRONTEND_NEXT_DIST / "assets"),
            name="assets",
        )
        # Mount the legacy /static path too so existing deep links and
        # screenshots from the old UI keep resolving during rollout.
        if (FRONTEND_LEGACY / "static").is_dir():
            app.mount(
                "/static",
                StaticFiles(directory=FRONTEND_LEGACY / "static"),
                name="static",
            )
        index_path = FRONTEND_NEXT_DIST / "index.html"
        logger.info("Serving Vite frontend from %s", FRONTEND_NEXT_DIST)
    else:
        app.mount(
            "/static",
            StaticFiles(directory=FRONTEND_LEGACY / "static"),
            name="static",
        )
        index_path = FRONTEND_LEGACY / "index.html"
        logger.info("Vite build not found; serving legacy frontend from %s", FRONTEND_LEGACY)

    @app.get("/", include_in_schema=False)
    def serve_ui():
        return FileResponse(index_path)

    return app


app = create_app()
