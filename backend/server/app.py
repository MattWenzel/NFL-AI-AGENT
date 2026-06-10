"""FastAPI application factory for nflverse database."""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.config import ALLOW_NULL_ORIGIN, ALLOWED_ORIGINS, DB_PATH
from backend.server.middleware import RequestIDMiddleware, SecurityHeadersMiddleware
from backend.server.startup import configure_runtime_state, log_environment_state, run_housekeeping, validate_encryption
from backend.api.routes import auth
from backend.api.routes import chat
from backend.api.routes import conversations
from backend.api.routes import database
from backend.api.routes import exports
from backend.api.routes import oauth_google
from backend.api.routes import oauth_openai
from backend.api.routes import providers
from backend.api.routes import settings
from backend.api.routes import tables

# The Vite/React build under frontend/dist is what the Docker image ships
# and what the production deploy serves. Local devs running `python3 run.py`
# without `npm run build` first will get a 404 at `/` until the build exists.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

logger = logging.getLogger(__name__)


def _init_sentry() -> None:
    """Initialize Sentry when SENTRY_DSN is set; silent no-op otherwise.

    Every string in the outgoing event runs through the same secret
    patterns as the log redactor, so a key pasted into a chat message
    that ends up in an exception never reaches Sentry in the clear.
    """
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("SENTRY_DSN set but sentry-sdk not installed — skipping")
        return

    from backend.server.logging import redact_secrets

    def _scrub(obj):
        if isinstance(obj, str):
            return redact_secrets(obj)
        if isinstance(obj, dict):
            return {k: _scrub(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_scrub(v) for v in obj]
        return obj

    def _before_send(event, hint):
        return _scrub(event)

    sentry_sdk.init(
        dsn=dsn,
        send_default_pii=False,
        traces_sample_rate=0.0,  # errors only — no perf tracing at this scale
        before_send=_before_send,
    )
    logger.info("Sentry initialized")


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
    _init_sentry()
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
    app.include_router(database.router)
    app.include_router(providers.router)
    app.include_router(exports.download_router)
    app.include_router(exports.router)

    # Deep health: SELECT 1 against both databases, cached so the 30s
    # probe interval (and any curious humans) can't thrash the DBs. A
    # detached volume or corrupted file flips this to 503, which makes
    # Fly's checks fail instead of routing traffic to a zombie.
    health_cache = {"checked_at": 0.0, "ok": True, "detail": "ok"}
    HEALTH_CACHE_SECONDS = 10

    def _check_duckdb() -> None:
        import duckdb

        conn = duckdb.connect(str(DB_PATH), read_only=True)
        try:
            conn.execute("SELECT 1")
        finally:
            conn.close()

    @app.get("/health")
    async def health_check():
        """Health check endpoint — verifies both databases respond."""
        now = time.monotonic()
        if now - health_cache["checked_at"] >= HEALTH_CACHE_SECONDS:
            ok, detail = True, "ok"
            try:
                await app.state.store.healthcheck()
            except Exception as exc:
                ok, detail = False, f"runtime db unreachable: {exc.__class__.__name__}"
            if ok:
                try:
                    await asyncio.to_thread(_check_duckdb)
                except Exception as exc:
                    ok, detail = False, f"stats db unreachable: {exc.__class__.__name__}"
            health_cache.update(checked_at=now, ok=ok, detail=detail)
            if not ok:
                logger.error("Health check failed: %s", detail)
        status_code = 200 if health_cache["ok"] else 503
        return JSONResponse(
            {"status": "ok" if health_cache["ok"] else "degraded", "detail": health_cache["detail"]},
            status_code=status_code,
        )

    # Serve the UI from the same origin as the API. Specific API routes above
    # take precedence; the mounts below only catch asset requests, the bare
    # root, and the SPA catchall for client-side routing.
    index_path = FRONTEND_DIST / "index.html"
    if index_path.is_file():
        app.mount(
            "/assets",
            StaticFiles(directory=FRONTEND_DIST / "assets"),
            name="assets",
        )
        logger.info("Serving frontend from %s", FRONTEND_DIST)

        @app.get("/", include_in_schema=False)
        def serve_ui():
            return FileResponse(index_path)

        # SPA catchall — serves index.html for any path not matched by an
        # API route or `/assets`. Lets the browser back/forward buttons
        # navigate between in-app surfaces (chats, reports, database).
        # Registered LAST so all specific routers above win first.
        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_spa(full_path: str):  # noqa: ARG001 — path captured for matching only
            return FileResponse(index_path)
    else:
        logger.warning(
            "Frontend build not found at %s — run `cd frontend && npm run build` "
            "to build it. API endpoints still work; `/` will 404.",
            FRONTEND_DIST,
        )

    return app


app = create_app()
