"""Startup/bootstrap helpers for the FastAPI app."""

from __future__ import annotations

import logging
import os
import signal
import time
from fastapi import FastAPI
from sqlalchemy import text

from backend.domain.agent.runtime import ChatRuntime
from backend.domain.auth import encryption
from backend.config import (
    ALLOWED_ORIGINS,
    APP_BASE_URL,
    DB_PATH,
    EMAIL_FROM_ADDRESS,
    EMAIL_VERIFICATION_REQUIRED,
    EXPORTS_DIR,
    RESEND_API_KEY,
    RUNTIME_DB_PATH,
    format_file_size,
    google_oauth_enabled,
)
from backend.domain.auth.types import OAUTH_ONLY_SENTINEL_HASH, PASSWORD
from backend.domain.providers import list_providers
from backend.server.process_state import AppProcessState
from backend.data import IdentityConflictError, RuntimeStore

logger = logging.getLogger(__name__)


def configure_runtime_state(app: FastAPI) -> None:
    store = RuntimeStore(RUNTIME_DB_PATH)
    app.state.store = store
    app.state.chat_runtime = ChatRuntime(store)
    app.state.process_state = AppProcessState()
    _install_drain_signal_handler(app.state.process_state)
    # Log the runtime schema version so a bug report's logs show which
    # migrations this DB has — saves a round of "what version are you on?".
    with store._sync_engine.connect() as conn:
        version = conn.execute(text("PRAGMA user_version")).scalar()
    logger.info("runtime db schema version: %s", version)


def _install_drain_signal_handler(process_state: AppProcessState) -> None:
    """Chain a SIGTERM handler that flips `process_state.draining`.

    Uvicorn waits `--timeout-graceful-shutdown` seconds for open
    connections after SIGTERM; during that window the SSE loops poll
    `draining` and end their streams with a structured
    `server_restarting` event instead of a dead socket. We chain to
    uvicorn's own handler (installed before the lifespan runs) so its
    shutdown sequencing is untouched.

    Best-effort: in test harnesses or non-main threads, signal
    installation fails — skip silently, the flag just never flips.
    """
    try:
        previous = signal.getsignal(signal.SIGTERM)

        def _handler(signum, frame):
            process_state.draining = True
            logger.info("SIGTERM received — draining active SSE streams")
            if callable(previous):
                previous(signum, frame)
            elif previous == signal.SIG_DFL:
                signal.signal(signal.SIGTERM, signal.SIG_DFL)
                signal.raise_signal(signal.SIGTERM)

        signal.signal(signal.SIGTERM, _handler)
    except (ValueError, OSError, RuntimeError):
        # ValueError: not in the main thread (e.g. TestClient's portal).
        logger.debug("Drain signal handler not installed", exc_info=True)


def validate_encryption() -> None:
    try:
        encryption.require_configured()
    except encryption.EncryptionKeyMissing as exc:
        logger.error("%s", exc)
        raise
    except encryption.EncryptionKeyInvalid as exc:
        logger.error("%s", exc)
        raise


async def run_housekeeping(app: FastAPI) -> None:
    store: RuntimeStore = app.state.store

    purged = await store.purge_expired_auth_sessions()
    if purged:
        logger.info("Purged %d expired auth session(s)", purged)

    user_count = await store.count_users()
    if user_count == 0:
        logger.info("No users registered yet — first visitor to the UI will be prompted to create an account.")
        return

    logger.info("%d user account(s) registered", user_count)
    promoted = await store.ensure_admin_exists()
    if promoted is not None:
        logger.info("Promoted user %d to admin (no admin existed yet)", promoted)
    orphans = await store.count_orphan_rows()
    if any(orphans.values()):
        logger.warning(
            "Found orphan rows with NULL user_id — invisible to scoped queries: %s",
            orphans,
        )

    seeded = await _seed_password_identities(store)
    if seeded:
        logger.info("Seeded 'password' identity for %d pre-existing user(s)", seeded)

    await _sweep_orphan_exports(store)


async def _sweep_orphan_exports(store: RuntimeStore) -> None:
    """Reconcile EXPORTS_DIR against the exports table.

    A crash between the CSV write and the DB row registration (the window
    is documented at `turn.py`'s register_export bridge) leaves a file no
    one can see or delete from the UI. Sweep: delete files with no row,
    but only when older than an hour so we never race an in-flight
    export. Rows whose file vanished are logged — the library entry still
    renders and its delete button cleans up the row.
    """
    if not EXPORTS_DIR.is_dir():
        return
    known = {record.filename for record in await store.list_exports()}
    now = time.time()
    removed = 0
    for path in EXPORTS_DIR.glob("*.csv"):
        if path.name in known:
            known.discard(path.name)
            continue
        try:
            if now - path.stat().st_mtime < 3600:
                continue
            path.unlink()
            removed += 1
        except OSError:
            logger.warning("Could not remove orphan export %s", path.name)
    if removed:
        logger.info("Removed %d orphan export file(s) with no library row", removed)
    # Whatever is left in `known` is a row without a file.
    missing = {f for f in known if not (EXPORTS_DIR / f).exists()}
    if missing:
        logger.warning(
            "%d export row(s) reference missing files: %s",
            len(missing),
            sorted(missing)[:10],
        )


async def _seed_password_identities(store: RuntimeStore) -> int:
    """Ensure every password-created user has a matching user_identities row.

    After the 0004 migration lands, the settings UI lists identities; without
    seeding, existing password users would show up as having no identities
    at all and could be tempted to "add one" via OAuth and then find they
    can't remove Google later. Idempotent — skips users who already have a
    `password` identity.
    """
    count = 0
    users = await store.list_users()
    for user in users:
        if user.password_hash == OAUTH_ONLY_SENTINEL_HASH:
            continue  # OAuth-only account — no password identity expected
        existing = await store.get_identity(user_id=user.id, provider=PASSWORD)
        if existing is not None:
            continue
        try:
            await store.create_identity(
                user_id=user.id,
                provider=PASSWORD,
                provider_subject=user.email,
                email=user.email,
            )
            count += 1
        except IdentityConflictError:
            # Another row already owns this (email, provider='password') pair —
            # shouldn't happen since emails are unique, but don't crash startup.
            logger.warning(
                "Could not seed password identity for user %d — conflict", user.id
            )
    return count


def log_environment_state() -> None:
    if DB_PATH.exists():
        logger.info("nflverse.duckdb: %s (%s)", DB_PATH, format_file_size(DB_PATH.stat().st_size))
    else:
        logger.warning("nflverse.duckdb NOT FOUND at %s — API endpoints will fail", DB_PATH)

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
        logger.info("runtime db: %s (%s)", RUNTIME_DB_PATH, format_file_size(RUNTIME_DB_PATH.stat().st_size))

    # Security posture sanity checks — loud warnings when a prod-shaped
    # deployment is missing the hardened env vars.
    looks_prod = APP_BASE_URL.startswith("https://") and "localhost" not in APP_BASE_URL
    if looks_prod and not ALLOWED_ORIGINS:
        logger.warning(
            "ALLOWED_ORIGINS is unset on a prod-shaped APP_BASE_URL (%s) — "
            "falling back to localhost defaults. Set ALLOWED_ORIGINS=<your-origin>.",
            APP_BASE_URL,
        )
    if EMAIL_VERIFICATION_REQUIRED and (not RESEND_API_KEY or not EMAIL_FROM_ADDRESS):
        logger.error(
            "EMAIL_VERIFICATION_REQUIRED=1 but RESEND_API_KEY/EMAIL_FROM_ADDRESS unset — "
            "new users will not receive verification emails and will be unable to log in."
        )
    if not EMAIL_VERIFICATION_REQUIRED:
        logger.info("Email verification: disabled (EMAIL_VERIFICATION_REQUIRED=0)")
    else:
        logger.info("Email verification: enabled — login requires a verified email")

    if google_oauth_enabled():
        logger.info("Google OAuth: enabled — 'Continue with Google' button active")
    else:
        logger.info(
            "Google OAuth: disabled (set GOOGLE_OAUTH_CLIENT_ID + GOOGLE_OAUTH_CLIENT_SECRET to enable)"
        )
