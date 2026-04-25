"""Codex (ChatGPT) OAuth endpoints — device-code flow."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core.auth.types import AuthenticatedUser
from app.bootstrap.csrf import verify_csrf
from app.bootstrap.dependencies import get_codex_oauth_service, get_current_user, get_process_state
from app.bootstrap.process_state import AppProcessState
from app.processes.oauth.codex.schemas import CodexOAuthStartResponse, CodexOAuthStatusResponse
from app.processes.oauth.codex.service import CodexOAuthService
from app.processes.oauth.codex.errors import CodexOAuthUnknownFlowError, CodexOAuthUpstreamError

router = APIRouter(prefix="/settings/oauth/codex", tags=["settings"], dependencies=[Depends(verify_csrf)])


@router.post("/start", response_model=CodexOAuthStartResponse)
async def start_codex_oauth(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CodexOAuthService = Depends(get_codex_oauth_service),
    process_state: AppProcessState = Depends(get_process_state),
) -> CodexOAuthStartResponse:
    process_state.codex_start_limiter.check(request)
    try:
        return await service.start(user_id=user.id)
    except CodexOAuthUpstreamError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach OpenAI device-code endpoint: {exc}",
        )


@router.get("/status", response_model=CodexOAuthStatusResponse)
async def status_codex_oauth(
    pending_id: str = Query(..., min_length=16, max_length=64),
    service: CodexOAuthService = Depends(get_codex_oauth_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> CodexOAuthStatusResponse:
    try:
        return await service.status(pending_id=pending_id, user_id=user.id)
    except CodexOAuthUnknownFlowError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/cancel")
async def cancel_codex_oauth(
    pending_id: str = Query(..., min_length=16, max_length=64),
    service: CodexOAuthService = Depends(get_codex_oauth_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    try:
        await service.cancel(pending_id=pending_id, user_id=user.id)
    except CodexOAuthUnknownFlowError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True}
