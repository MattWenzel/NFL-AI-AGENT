"""Settings endpoints for per-user API key storage + linked OAuth identities."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.lib.auth.types import GOOGLE, AuthenticatedUser
from backend.server.csrf import verify_csrf
from backend.server.dependencies import (
    get_codex_oauth_service,
    get_current_user,
    get_google_oauth_service,
    get_process_state,
    get_settings_service,
)
from backend.server.request_context import audit_from_request, client_ip
from backend.server.process_state import AppProcessState
from backend.features.settings import (
    ApiKeyStatus,
    ApiKeyUpdate,
    IdentitySummaryResponse,
    LinkGoogleStartResponse,
)
from backend.features.oauth.codex.schemas import (
    CodexOAuthStartResponse,
    CodexOAuthStatusResponse,
)
from backend.features.oauth.codex.service import (
    CodexOAuthService,
    CodexOAuthUnknownFlowError,
    CodexOAuthUpstreamError,
)
from backend.features.oauth.google.errors import (
    GoogleOAuthDisabledError,
    GoogleOAuthLastIdentityError,
    GoogleOAuthLinkConflictError,
    GoogleOAuthServiceError,
)
from backend.features.settings import (
    SettingsNotFoundError,
    SettingsServiceError,
)
from backend.features.oauth.google.service import GoogleOAuthService
from backend.features.settings import SettingsService

# CSRF applies to mutating routes on this router; the GET /api-keys listing
# is safe. Wiring at router level avoids per-route Depends sprawl.
router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(verify_csrf)])


@router.get("/api-keys", response_model=list[ApiKeyStatus])
async def list_api_key_status(
    user: AuthenticatedUser = Depends(get_current_user),
    service: SettingsService = Depends(get_settings_service),
) -> list[ApiKeyStatus]:
    return await service.list_api_key_status(user.id)


@router.put("/api-keys/{provider}", response_model=ApiKeyStatus)
async def update_api_key(
    provider: str,
    payload: ApiKeyUpdate,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SettingsService = Depends(get_settings_service),
    process_state: AppProcessState = Depends(get_process_state),
) -> ApiKeyStatus:
    # Rate-limit per-IP so a stolen token can't cycle keys infinitely. Reuses
    # the same limiter the account endpoints use — 5 attempts / 15 min.
    process_state.account_limiter.check(request)
    try:
        return await service.update_api_key(
            user_id=user.id,
            provider=provider,
            api_key=payload.api_key,
            audit_ip=client_ip(request),
            audit_user_agent=request.headers.get("User-Agent"),
        )
    except SettingsNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except SettingsServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# ---------------- Codex OAuth device flow ----------------


@router.post("/oauth/codex/start", response_model=CodexOAuthStartResponse)
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


@router.get("/oauth/codex/status", response_model=CodexOAuthStatusResponse)
async def status_codex_oauth(
    pending_id: str = Query(..., min_length=16, max_length=64),
    service: CodexOAuthService = Depends(get_codex_oauth_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> CodexOAuthStatusResponse:
    try:
        return await service.status(pending_id=pending_id, user_id=user.id)
    except CodexOAuthUnknownFlowError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/oauth/codex/cancel")
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


# ---------------- linked identities ----------------


@router.get("/identities", response_model=list[IdentitySummaryResponse])
async def list_identities(
    user: AuthenticatedUser = Depends(get_current_user),
    service: GoogleOAuthService = Depends(get_google_oauth_service),
) -> list[IdentitySummaryResponse]:
    rows = await service.list_identities(user.id)
    return [
        IdentitySummaryResponse(
            provider=r.provider,
            display=r.display,
            linked_at=r.linked_at,
            removable=r.removable,
        )
        for r in rows
    ]


@router.post("/identities/google/link", response_model=LinkGoogleStartResponse)
async def start_link_google(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GoogleOAuthService = Depends(get_google_oauth_service),
) -> LinkGoogleStartResponse:
    try:
        url = await service.begin_link(user_id=user.id, audit=audit_from_request(request))
    except GoogleOAuthDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except GoogleOAuthServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return LinkGoogleStartResponse(auth_url=url)


@router.delete("/identities/{provider}")
async def unlink_identity(
    provider: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GoogleOAuthService = Depends(get_google_oauth_service),
):
    if provider not in {GOOGLE}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown provider")
    try:
        await service.unlink(user_id=user.id, provider=provider, audit=audit_from_request(request))
    except GoogleOAuthLastIdentityError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except GoogleOAuthLinkConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return {"ok": True}
