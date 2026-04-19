"""Codex (ChatGPT) OAuth endpoints — device-code flow.

Three endpoints, all gated on `get_current_user`:

- `POST /settings/oauth/codex/start` — asks OpenAI for a user_code, spawns
  a background task that polls until sign-in completes or the 15-min
  window expires, returns display data for the UI.
- `GET  /settings/oauth/codex/status?pending_id=…` — polled by the UI;
  returns "pending", "complete" (+ email), "expired", or "error".
- `DELETE /settings/oauth/codex/cancel?pending_id=…` — cancel in-flight.

The localhost-pinned authorization-code flow can't reach a deployed web
app (OpenAI's public Codex client only whitelists `http://localhost:1455/
auth/callback`). Device-code sidesteps that entirely — the user signs in
on any device and enters a short code, no redirect URI involved. See
`infra/codex_oauth.py` for the wire contract.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from api.auth import AuthenticatedUser, get_current_user
from api.dependencies import CODEX_PROVIDER, get_store
from api.rate_limit import RateLimiter
from api.schemas import CodexOAuthStartResponse, CodexOAuthStatusResponse
from infra import codex_oauth, encryption
from infra.persistence.runtime_store import RuntimeStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings/oauth/codex", tags=["settings"])

# Rate limit device-code starts to curb abuse: 5 per IP per hour is
# generous for a real user (they'd never need more than a handful).
_start_limiter = RateLimiter(max_attempts=5, window_seconds=60 * 60)

# In-memory pending state. One record per active device-code flow; GC'd
# when the user reads a terminal status or when it ages past the
# device-code window. Not persisted — a server restart invalidates
# in-flight flows (the user just retries).
@dataclass
class _Pending:
    user_id: int
    device_auth_id: str
    user_code: str
    started_at: float  # monotonic
    task: asyncio.Task | None = None
    status: str = "pending"  # pending | complete | expired | error
    email: str | None = None
    error: str | None = None
    # Access lock for the rare case of /status and the poller finishing
    # at the same tick.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_pending: dict[str, _Pending] = {}

# How long we keep a terminal record around for the UI to read. Beyond
# this we evict; the UI should have polled by then. Matches the
# 15-minute device-code window so we never evict a still-live task.
_MAX_RECORD_AGE_SECONDS = 60 * 20


def _evict_stale() -> None:
    """Drop terminal records older than the window. Called from every handler."""
    now = time.monotonic()
    stale = [
        pid for pid, rec in _pending.items()
        if rec.status != "pending" and now - rec.started_at > _MAX_RECORD_AGE_SECONDS
    ]
    for pid in stale:
        _pending.pop(pid, None)


async def _run_device_flow(pending_id: str, store: RuntimeStore) -> None:
    """Poll the device-code endpoint, exchange the code for tokens, persist."""
    rec = _pending.get(pending_id)
    if rec is None:
        return
    try:
        authorized = await codex_oauth.poll_device_code(
            rec.device_auth_id, rec.user_code
        )
        bundle = await codex_oauth.exchange_code(
            authorized.authorization_code, authorized.code_verifier
        )
        ciphertext = encryption.encrypt(codex_oauth.bundle_to_json(bundle))
        store.upsert_api_key(
            user_id=rec.user_id,
            provider=CODEX_PROVIDER,
            encrypted_key=ciphertext,
        )
        async with rec.lock:
            rec.status = "complete"
            rec.email = bundle.email
        logger.info("Codex OAuth complete for user=%d email=%s", rec.user_id, bundle.email)
    except codex_oauth.DeviceCodeExpired:
        async with rec.lock:
            rec.status = "expired"
        logger.info("Codex OAuth expired for user=%d", rec.user_id)
    except asyncio.CancelledError:
        # /cancel was called — leave the record as "pending" since the
        # caller is already handling status in their own response.
        raise
    except Exception as exc:
        async with rec.lock:
            rec.status = "error"
            rec.error = str(exc)
        logger.exception("Codex OAuth failed for user=%d", rec.user_id)


@router.post("/start", response_model=CodexOAuthStartResponse)
async def start_codex_oauth(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    store: RuntimeStore = Depends(get_store),
) -> CodexOAuthStartResponse:
    _start_limiter.check(request)
    _evict_stale()

    try:
        start = await codex_oauth.request_device_code()
    except codex_oauth.CodexOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach OpenAI device-code endpoint: {exc}",
        )

    pending_id = uuid.uuid4().hex
    rec = _Pending(
        user_id=user.id,
        device_auth_id=start.device_auth_id,
        user_code=start.user_code,
        started_at=time.monotonic(),
    )
    _pending[pending_id] = rec
    rec.task = asyncio.create_task(_run_device_flow(pending_id, store))
    logger.info("Codex OAuth started for user=%d pending=%s", user.id, pending_id)
    return CodexOAuthStartResponse(
        pending_id=pending_id,
        user_code=start.user_code,
        verification_url=start.verification_url,
        expires_in=15 * 60,
    )


@router.get("/status", response_model=CodexOAuthStatusResponse)
async def status_codex_oauth(
    pending_id: str = Query(..., min_length=16, max_length=64),
    user: AuthenticatedUser = Depends(get_current_user),
) -> CodexOAuthStatusResponse:
    _evict_stale()
    rec = _pending.get(pending_id)
    if rec is None or rec.user_id != user.id:
        # Same-shape 404 whether the id is unknown or belongs to another user.
        raise HTTPException(status_code=404, detail="Unknown pending flow")
    async with rec.lock:
        snapshot = CodexOAuthStatusResponse(
            status=rec.status, email=rec.email, error=rec.error
        )
    # If terminal, drop the record so the UI can't re-read it (the UI
    # has everything it needs from this response). Keeps the dict lean.
    if snapshot.status != "pending":
        _pending.pop(pending_id, None)
    return snapshot


@router.delete("/cancel")
async def cancel_codex_oauth(
    pending_id: str = Query(..., min_length=16, max_length=64),
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    rec = _pending.pop(pending_id, None)
    if rec is None or rec.user_id != user.id:
        raise HTTPException(status_code=404, detail="Unknown pending flow")
    if rec.task and not rec.task.done():
        rec.task.cancel()
        try:
            await rec.task
        except (asyncio.CancelledError, Exception):
            pass
    return {"ok": True}
