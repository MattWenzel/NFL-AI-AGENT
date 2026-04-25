"""Codex OAuth process: schemas, errors, credential refresh, and device flow."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass

from pydantic import BaseModel, Field

from backend.persistence import AuditEvent, RuntimeStore
from backend.providers.types import CODEX
from backend.runtime_state import PendingCodexOAuthFlows, PerUserLockRegistry
from backend.security import codex_oauth, encryption
from backend.security.errors import CodexOAuthError, DeviceCodeExpired
from backend.security.types import TokenBundle

logger = logging.getLogger(__name__)

MAX_RECORD_AGE_SECONDS = 60 * 20


class CodexOAuthServiceError(Exception):
    pass


class CodexOAuthUnknownFlowError(CodexOAuthServiceError):
    pass


class CodexOAuthUpstreamError(CodexOAuthServiceError):
    pass


class CodexCredentialError(Exception):
    """Raised when a stored Codex connection exists but refresh fails."""


class CodexOAuthStartResponse(BaseModel):
    pending_id: str
    user_code: str
    verification_url: str
    expires_in: int = Field(900, description="Seconds until the user_code expires")


class CodexOAuthStatusResponse(BaseModel):
    status: str = Field(..., description="pending | complete | expired | error")
    email: str | None = None
    error: str | None = None


async def _load_bundle(
    store: RuntimeStore, user_id: int, provider_name: str
) -> TokenBundle | None:
    rec = await store.get_api_key(user_id=user_id, provider=provider_name)
    if rec is None:
        return None
    try:
        blob_json = encryption.decrypt(rec.encrypted_key)
    except ValueError:
        logger.error("Failed to decrypt stored Codex bundle for user=%d", user_id)
        return None
    try:
        return codex_oauth.bundle_from_json(blob_json)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        logger.error("Malformed Codex bundle for user=%d: %s", user_id, exc)
        return None


async def resolve_access_token(
    store: RuntimeStore,
    user_id: int,
    provider_name: str,
    *,
    refresh_locks: PerUserLockRegistry,
) -> str | None:
    bundle = await _load_bundle(store, user_id, provider_name)
    if bundle is None:
        return None
    if not codex_oauth.is_near_expiry(bundle):
        return bundle.access_token
    async with refresh_locks.for_user(user_id):
        bundle = await _load_bundle(store, user_id, provider_name)
        if bundle is None:
            return None
        if not codex_oauth.is_near_expiry(bundle):
            return bundle.access_token
        try:
            bundle = await codex_oauth.refresh_access_token(bundle.refresh_token)
        except CodexOAuthError as exc:
            logger.warning("Codex token refresh failed for user=%d: %s", user_id, exc)
            raise CodexCredentialError("ChatGPT session expired — reconnect in Settings.") from exc
        await store.upsert_api_key(
            user_id=user_id,
            provider=provider_name,
            encrypted_key=encryption.encrypt(codex_oauth.bundle_to_json(bundle)),
        )
        logger.info("Refreshed Codex token for user=%d", user_id)
        return bundle.access_token


@dataclass
class CodexOAuthService:
    store: RuntimeStore
    pending_flows: PendingCodexOAuthFlows

    async def run_device_flow(self, pending_id: str) -> None:
        rec = self.pending_flows.get(pending_id)
        if rec is None:
            return
        try:
            authorized = await codex_oauth.poll_device_code(rec.device_auth_id, rec.user_code)
            bundle = await codex_oauth.exchange_code(authorized.authorization_code, authorized.code_verifier)
            ciphertext = encryption.encrypt(codex_oauth.bundle_to_json(bundle))
            await self.store.upsert_api_key(
                user_id=rec.user_id,
                provider=CODEX,
                encrypted_key=ciphertext,
            )
            await self.store.record_security_event(
                event_type=AuditEvent.OAUTH_LINKED,
                user_id=rec.user_id,
                metadata={"provider": "codex", "identity_email": bundle.email},
            )
            async with rec.lock:
                rec.status = "complete"
                rec.email = bundle.email
        except DeviceCodeExpired:
            async with rec.lock:
                rec.status = "expired"
        except asyncio.CancelledError:
            raise
        except (CodexOAuthError, ValueError):
            logger.exception("Codex OAuth failed for user=%d", rec.user_id)
            async with rec.lock:
                rec.status = "error"
                rec.error = "Sign-in failed — try again."

    async def start(self, *, user_id: int) -> CodexOAuthStartResponse:
        self.pending_flows.evict_terminal_older_than(MAX_RECORD_AGE_SECONDS)
        try:
            start = await codex_oauth.request_device_code()
        except CodexOAuthError as exc:
            raise CodexOAuthUpstreamError(str(exc)) from exc
        pending_id = uuid.uuid4().hex
        rec = self.pending_flows.create(
            pending_id,
            user_id=user_id,
            device_auth_id=start.device_auth_id,
            user_code=start.user_code,
        )
        rec.task = asyncio.create_task(self.run_device_flow(pending_id))
        return CodexOAuthStartResponse(
            pending_id=pending_id,
            user_code=start.user_code,
            verification_url=start.verification_url,
            expires_in=15 * 60,
        )

    async def status(self, *, pending_id: str, user_id: int) -> CodexOAuthStatusResponse:
        self.pending_flows.evict_terminal_older_than(MAX_RECORD_AGE_SECONDS)
        rec = self.pending_flows.get(pending_id)
        if rec is None or rec.user_id != user_id:
            raise CodexOAuthUnknownFlowError("Unknown pending flow")
        async with rec.lock:
            snapshot = CodexOAuthStatusResponse(status=rec.status, email=rec.email, error=rec.error)
        if snapshot.status != "pending":
            self.pending_flows.pop(pending_id)
        return snapshot

    async def cancel(self, *, pending_id: str, user_id: int) -> None:
        rec = self.pending_flows.get(pending_id)
        if rec is None or rec.user_id != user_id:
            raise CodexOAuthUnknownFlowError("Unknown pending flow")
        self.pending_flows.pop(pending_id)
        if rec.task and not rec.task.done():
            rec.task.cancel()
            try:
                await rec.task
            except asyncio.CancelledError:
                pass
