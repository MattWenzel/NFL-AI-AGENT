"""Auth endpoints: status, register, login, logout."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from auth.primitives import (
    AuthenticatedUser,
    _extract_bearer,
)
from config import EXPORTS_DIR
from server.dependencies import (
    get_current_user,
    get_current_user_optional,
    get_process_state,
    get_store,
)
from server.process_state import AppProcessState
from server.schemas.auth import (
    AuthOkResponse,
    AuthStatusResponse,
    AuthTokenResponse,
    DeleteAccountRequest,
    LoginRequest,
    PasswordChangeRequest,
    RegisterRequest,
)
from server.services.auth import (
    AuthApplicationService,
    AuthConflictError,
    AuthCredentialsError,
    AuthValidationError,
)
from storage import RuntimeStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(
    store: RuntimeStore = Depends(get_store),
    user: AuthenticatedUser | None = Depends(get_current_user_optional),
) -> AuthStatusResponse:
    service = AuthApplicationService(store, EXPORTS_DIR)
    return await service.auth_status(user)


@router.post("/register", response_model=AuthTokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    store: RuntimeStore = Depends(get_store),
    process_state: AppProcessState = Depends(get_process_state),
) -> AuthTokenResponse:
    process_state.register_limiter.check(request)
    service = AuthApplicationService(store, EXPORTS_DIR)
    try:
        return await service.register(
            email=payload.email,
            password=payload.password,
            invite_code=payload.invite_code,
        )
    except AuthConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except AuthValidationError as exc:
        message = str(exc)
        status_code = status.HTTP_403_FORBIDDEN if message == "Invalid invite code" else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=message)


@router.post("/login", response_model=AuthTokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    store: RuntimeStore = Depends(get_store),
    process_state: AppProcessState = Depends(get_process_state),
) -> AuthTokenResponse:
    process_state.login_limiter.check(request)
    service = AuthApplicationService(store, EXPORTS_DIR)
    try:
        return await service.login(email=payload.email, password=payload.password)
    except AuthCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))


@router.post("/logout")
async def logout(
    request: Request,
    store: RuntimeStore = Depends(get_store),
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    service = AuthApplicationService(store, EXPORTS_DIR)
    await service.logout(_extract_bearer(request))
    return {"ok": True}


@router.put("/password", response_model=AuthOkResponse)
async def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    store: RuntimeStore = Depends(get_store),
    process_state: AppProcessState = Depends(get_process_state),
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthOkResponse:
    process_state.account_limiter.check(request)
    service = AuthApplicationService(store, EXPORTS_DIR)
    try:
        await service.change_password(
            user=user,
            current_password=payload.current_password,
            new_password=payload.new_password,
            keep_token=_extract_bearer(request) or "",
        )
    except AuthCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    return AuthOkResponse(ok=True)


@router.delete("/me", response_model=AuthOkResponse)
async def delete_account(
    payload: DeleteAccountRequest,
    request: Request,
    store: RuntimeStore = Depends(get_store),
    process_state: AppProcessState = Depends(get_process_state),
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthOkResponse:
    process_state.account_limiter.check(request)
    service = AuthApplicationService(store, EXPORTS_DIR)
    try:
        deleted_files = await service.delete_account(user=user, password=payload.password)
    except AuthCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    logger.info("Deleted user %d (%s); %d CSV file(s) removed", user.id, user.email, deleted_files)
    return AuthOkResponse(ok=True)
