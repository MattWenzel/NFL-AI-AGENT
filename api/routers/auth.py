"""OAuth auth endpoints (currently: Codex).

The public Codex CLI OAuth client is registered against
`http://localhost:1455/auth/callback`, not our FastAPI port. So /login kicks
off a short-lived loopback capture server in a background task and returns
the authorize URL for the browser to visit. The UI then polls /status until
the background task finishes.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.oauth.codex_auth import CodexAuth
from agent.oauth.login_server import LoopbackCaptureError, run_loopback_capture
from agent.oauth.token_store import TokenStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/codex", tags=["auth"])


# ---------- Module-level state ----------

@dataclass
class _ActiveLogin:
    state: str
    task: asyncio.Task


_active_login: _ActiveLogin | None = None
_active_login_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None
_last_login_error: str | None = None


def _login_lock() -> asyncio.Lock:
    global _active_login_lock
    loop = asyncio.get_running_loop()
    if _active_login_lock is None or _active_login_lock[0] is not loop:
        _active_login_lock = (loop, asyncio.Lock())
    return _active_login_lock[1]


async def _cancel_and_await(task: asyncio.Task) -> None:
    """Cancel a task and await its completion, swallowing CancelledError.

    Unexpected errors are logged but not raised — cancellation teardown should
    never mask the reason we were cancelling.
    """
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.warning("Error awaiting cancelled login task: %s", exc, exc_info=True)


def _store() -> TokenStore:
    """Load the token store at call time so tests can monkeypatch CODEX_AUTH_PATH."""
    from config import CODEX_AUTH_PATH  # noqa: PLC0415
    return TokenStore(CODEX_AUTH_PATH)


# ---------- Response models ----------

class LoginResponse(BaseModel):
    authorize_url: str
    state: str


class StatusResponse(BaseModel):
    authenticated: bool
    email: str | None = None
    expires_at_ms: int | None = None
    login_in_progress: bool = False
    error: str | None = None


class LogoutResponse(BaseModel):
    status: str


# ---------- Endpoints ----------

@router.post("/login", response_model=LoginResponse)
async def start_login():
    """Start an OAuth login. Returns the authorize URL for the browser."""
    global _active_login, _last_login_error
    async with _login_lock():
        # If an earlier attempt is still waiting, cancel it so we can rebind port 1455.
        if _active_login is not None and not _active_login.task.done():
            logger.info("Cancelling in-progress Codex login to start a new one")
            await _cancel_and_await(_active_login.task)

        _last_login_error = None
        auth = CodexAuth(_store())
        req = auth.build_authorize_request()
        task = asyncio.create_task(_drive_login(auth, req.state, req.verifier))
        _active_login = _ActiveLogin(state=req.state, task=task)
        logger.info("Codex login started, state=%s", req.state[:8])
        return LoginResponse(authorize_url=req.url, state=req.state)


@router.get("/status", response_model=StatusResponse)
async def get_status():
    """Report the current auth state and any in-progress login."""
    record = _store().get()
    active = _active_login
    in_progress = active is not None and not active.task.done()
    error = _last_login_error
    if record is None:
        return StatusResponse(
            authenticated=False,
            login_in_progress=in_progress,
            error=error,
        )
    return StatusResponse(
        authenticated=True,
        email=record.email,
        expires_at_ms=record.expires_at_ms,
        login_in_progress=in_progress,
        error=error,
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout():
    """Clear the stored Codex credentials."""
    if _store().delete():
        logger.info("Codex credentials cleared")
        return LogoutResponse(status="logged_out")
    raise HTTPException(status_code=404, detail="Not authenticated")


# ---------- Background login driver ----------

async def _drive_login(auth: CodexAuth, expected_state: str, verifier: str) -> None:
    """Wait for the loopback callback, validate state, exchange code for tokens."""
    global _active_login, _last_login_error
    try:
        result = await run_loopback_capture(timeout_seconds=300.0)
        if result.state != expected_state:
            raise LoopbackCaptureError("State mismatch — possible CSRF, aborting")
        record = await auth.exchange_code(result.code, verifier)
        logger.info("Codex login completed (email=%s)", record.email or "?")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Codex login failed: %s", exc)
        _last_login_error = str(exc)
    finally:
        if _active_login is not None and _active_login.state == expected_state:
            _active_login = None
        await auth.aclose()


async def cancel_active_login() -> None:
    """Cancel any in-flight OAuth login task."""
    global _active_login
    async with _login_lock():
        if _active_login is None or _active_login.task.done():
            _active_login = None
            return
        try:
            await _cancel_and_await(_active_login.task)
        finally:
            _active_login = None


# ---------- Test helpers ----------

def _reset_state_for_tests() -> None:
    """Clear the module-level active-login state. Tests only."""
    global _active_login, _active_login_lock, _last_login_error
    _active_login = None
    _active_login_lock = None
    _last_login_error = None
