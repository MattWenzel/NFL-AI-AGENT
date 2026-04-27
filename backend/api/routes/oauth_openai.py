"""Unauthenticated "Sign in with ChatGPT" device-code endpoints.

Wraps the existing CodexOAuthService device flow with a sign-in path
that doesn't require an existing session. The browser:
  1. POST /auth/oauth/openai/start    → {pending_id, user_code, verification_url}
  2. (user enters the code at OpenAI) → background task exchanges + resolves identity
  3. GET  /auth/oauth/openai/status   → polled until status='complete'
  4. On complete, this endpoint sets the session cookie and returns
     {token, user} so the SPA can transition to the authed view.

Cancel is exposed for the "X" button in the UI when a user gives up
mid-flow. Status/cancel are keyed on `pending_id` only (no session
required) — safe because pending_id is an unguessable 32-char hex.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from backend.api.dependencies import get_codex_oauth_service, get_process_state
from backend.api.schemas.auth import AuthTokenResponse, AuthUser
from backend.application.oauth.codex import (
    CodexOAuthService,
    CodexOAuthServiceError,
    CodexOAuthUnknownFlowError,
    CodexOAuthUpstreamError,
)
from backend.server.process_state import AppProcessState
from backend.server.session import set_auth_cookies

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/oauth/openai", tags=["auth"])


@router.post("/start")
async def start(
    request: Request,
    service: CodexOAuthService = Depends(get_codex_oauth_service),
    process_state: AppProcessState = Depends(get_process_state),
) -> dict:
    # Same per-IP cap as the link-flow start to stop a script burning
    # device-code allocations.
    process_state.codex_start_limiter.check(request)
    try:
        return await service.start_signin()
    except CodexOAuthUpstreamError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get("/status")
async def status_(
    request: Request,
    pending_id: str,
    service: CodexOAuthService = Depends(get_codex_oauth_service),
):
    try:
        snapshot = await service.signin_status(pending_id=pending_id)
    except CodexOAuthUnknownFlowError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except CodexOAuthServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if snapshot.status != "complete" or snapshot.session is None:
        # Pending / expired / error → just return the snapshot fields.
        return {
            "status": snapshot.status,
            "email": snapshot.email,
            "error": snapshot.error,
        }

    body = {
        "status": "complete",
        "email": snapshot.email,
        **AuthTokenResponse(
            token=snapshot.session.token,
            user=AuthUser(
                id=snapshot.user_id or 0,
                email=snapshot.user_email or "",
                role=snapshot.user_role or "user",
            ),
        ).model_dump(),
    }
    response = JSONResponse(content=body)
    set_auth_cookies(response=response, session=snapshot.session, request=request)
    return response


@router.delete("/cancel")
async def cancel(
    pending_id: str,
    service: CodexOAuthService = Depends(get_codex_oauth_service),
) -> dict:
    try:
        await service.cancel_signin(pending_id=pending_id)
    except CodexOAuthUnknownFlowError:
        # Already gone — idempotent.
        pass
    return {"ok": True}
