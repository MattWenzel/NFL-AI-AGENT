"""Application service for Codex OAuth device-code flow."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from auth import codex_oauth, encryption
from server.process_state import PendingCodexOAuthFlowStore
from server.repositories import UserRepository
from server.schemas.codex_oauth import CodexOAuthStartResponse, CodexOAuthStatusResponse

logger = logging.getLogger(__name__)

CODEX_PROVIDER = "openai-codex"
MAX_RECORD_AGE_SECONDS = 60 * 20


class CodexOAuthServiceError(Exception):
    pass


class CodexOAuthUnknownFlowError(CodexOAuthServiceError):
    pass


class CodexOAuthUpstreamError(CodexOAuthServiceError):
    pass


@dataclass
class CodexOAuthApplicationService:
    users: UserRepository
    pending_flows: PendingCodexOAuthFlowStore

    async def run_device_flow(self, pending_id: str) -> None:
        rec = self.pending_flows.get(pending_id)
        if rec is None:
            return
        try:
            authorized = await codex_oauth.poll_device_code(rec.device_auth_id, rec.user_code)
            bundle = await codex_oauth.exchange_code(authorized.authorization_code, authorized.code_verifier)
            ciphertext = encryption.encrypt(codex_oauth.bundle_to_json(bundle))
            await self.users.upsert_api_key(
                user_id=rec.user_id,
                provider=CODEX_PROVIDER,
                encrypted_key=ciphertext,
            )
            async with rec.lock:
                rec.status = "complete"
                rec.email = bundle.email
        except codex_oauth.DeviceCodeExpired:
            async with rec.lock:
                rec.status = "expired"
        except asyncio.CancelledError:
            raise
        except (codex_oauth.CodexOAuthError, ValueError):
            logger.exception("Codex OAuth failed for user=%d", rec.user_id)
            async with rec.lock:
                rec.status = "error"
                rec.error = "Sign-in failed — try again."

    async def start(self, *, user_id: int) -> CodexOAuthStartResponse:
        self.pending_flows.evict_terminal_older_than(MAX_RECORD_AGE_SECONDS)
        try:
            start = await codex_oauth.request_device_code()
        except codex_oauth.CodexOAuthError as exc:
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
