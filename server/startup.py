"""Startup/bootstrap helpers for the FastAPI app."""

from __future__ import annotations

import logging
import os
from fastapi import FastAPI

from agent.runtime import ChatRuntime
from auth import encryption
from config import DB_PATH, PBP_DB_PATH, RUNTIME_DB_PATH, format_file_size
from provider import list_providers
from server.process_state import AppProcessState
from server.repositories import RepositoryBundle
from agent.runtime_repositories import RuntimeRepositoryBundle
from storage import RuntimeStore

logger = logging.getLogger(__name__)


def configure_runtime_state(app: FastAPI) -> None:
    store = RuntimeStore(RUNTIME_DB_PATH)
    app.state.repositories = RepositoryBundle.from_store(store)
    app.state.runtime_repositories = RuntimeRepositoryBundle.from_store(store)
    app.state.chat_runtime = ChatRuntime(app.state.runtime_repositories)
    app.state.process_state = AppProcessState()


def validate_encryption() -> None:
    try:
        encryption.require_configured()
    except encryption.EncryptionKeyMissing as exc:
        logger.error("%s", exc)
        raise
    except encryption.EncryptionKeyInvalid as exc:
        logger.error("%s", exc)
        raise


def run_housekeeping(app: FastAPI) -> None:
    users = app.state.repositories.users

    purged = users.purge_expired_auth_sessions_sync()
    if purged:
        logger.info("Purged %d expired auth session(s)", purged)

    user_count = users.count_users_sync()
    if user_count == 0:
        logger.info("No users registered yet — first visitor to the UI will be prompted to create an account.")
        return

    logger.info("%d user account(s) registered", user_count)
    promoted = users.ensure_admin_exists_sync()
    if promoted is not None:
        logger.info("Promoted user %d to admin (no admin existed yet)", promoted)
    orphans = users.count_orphan_rows_sync()
    if any(orphans.values()):
        logger.warning(
            "Found orphan rows with NULL user_id — invisible to scoped queries: %s",
            orphans,
        )


def log_environment_state() -> None:
    if DB_PATH.exists():
        logger.info("nflverse.db: %s (%s)", DB_PATH, format_file_size(DB_PATH.stat().st_size))
    else:
        logger.warning("nflverse.db NOT FOUND at %s — API endpoints will fail", DB_PATH)

    if PBP_DB_PATH.exists():
        logger.info("pbp.db: %s (%s)", PBP_DB_PATH, format_file_size(PBP_DB_PATH.stat().st_size))
    else:
        logger.warning("pbp.db not found at %s — PBP queries will fail", PBP_DB_PATH)

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
