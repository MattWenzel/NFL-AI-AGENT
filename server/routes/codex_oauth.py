"""Codex (ChatGPT) OAuth endpoints — device-code flow."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from auth import codex_oauth
from auth.primitives import AuthenticatedUser, get_current_user
from server.dependencies import get_user_repository
from server.process_state import AppProcessState, get_process_state
from server.repositories import UserRepository
from server.schemas.codex_oauth import CodexOAuthStartResponse, CodexOAuthStatusResponse
from server.services.codex_oauth import (
    CodexOAuthApplicationService,
    CodexOAuthUnknownFlowError,
)

router = APIRouter(prefix="/settings/oauth/codex", tags=["settings"])


@router.post("/start", response_model=CodexOAuthStartResponse)
async def start_codex_oauth(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    users: UserRepository = Depends(get_user_repository),
    process_state: AppProcessState = Depends(get_process_state),
) -> CodexOAuthStartResponse:
    process_state.codex_start_limiter.check(request)
    service = CodexOAuthApplicationService(users, process_state)
    try:
        return await service.start(user_id=user.id)
    except codex_oauth.CodexOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach OpenAI device-code endpoint: {exc}",
        )


@router.get("/status", response_model=CodexOAuthStatusResponse)
async def status_codex_oauth(
    pending_id: str = Query(..., min_length=16, max_length=64),
    process_state: AppProcessState = Depends(get_process_state),
    user: AuthenticatedUser = Depends(get_current_user),
    users: UserRepository = Depends(get_user_repository),
) -> CodexOAuthStatusResponse:
    service = CodexOAuthApplicationService(users, process_state)
    try:
        return await service.status(pending_id=pending_id, user_id=user.id)
    except CodexOAuthUnknownFlowError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/cancel")
async def cancel_codex_oauth(
    pending_id: str = Query(..., min_length=16, max_length=64),
    process_state: AppProcessState = Depends(get_process_state),
    user: AuthenticatedUser = Depends(get_current_user),
    users: UserRepository = Depends(get_user_repository),
) -> dict:
    service = CodexOAuthApplicationService(users, process_state)
    try:
        await service.cancel(pending_id=pending_id, user_id=user.id)
    except CodexOAuthUnknownFlowError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True}
