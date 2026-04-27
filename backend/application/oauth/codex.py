"""Codex OAuth: device-code flow orchestration plus stored-bundle access-token resolution.

Two device-code paths share this service:

  - **Authenticated linking** (`start(user_id=...)`) — the user is already
    signed into the app (via password or Google) and is connecting their
    ChatGPT account in Settings. The bundle is encrypted and stored at
    `api_keys[user, "openai-codex"]`.
  - **Sign in with ChatGPT** (`start_signin(...)`) — the user has no app
    session yet. After the bundle arrives, we extract the `chatgpt_user_id`
    + email claims, resolve to an existing user (by sub or email) or
    create a new account, store the bundle as that user's Codex
    credential, issue an auth session, and stash the session token on
    the pending-flow record so the unauthenticated polling endpoint can
    hand it to the browser.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass

from backend.domain.auth import codex_oauth, encryption
from backend.domain.auth.errors import AuthConflictError, CodexOAuthError, DeviceCodeExpired
from backend.domain.auth.lifecycle import IdentitySeed, create_user_account, issue_session
from backend.domain.auth.types import IssuedSession, OAUTH_ONLY_SENTINEL_HASH, OPENAI, TokenBundle
from backend.domain.providers.types import CODEX
from backend.data import AuditEvent, IdentityConflictError, RuntimeStore
from backend.runtime_state import PendingCodexOAuthFlows, PerUserLockRegistry


@dataclass
class _ResolvedSignin:
    user_id: int
    user_email: str
    user_role: str
    session: IssuedSession


@dataclass
class _SigninStatusSnapshot:
    status: str
    email: str | None
    error: str | None
    user_id: int | None
    user_email: str | None
    user_role: str | None
    session: IssuedSession | None

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


# --- credential resolution (used by ProviderCredentialService) ---------------


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


# --- device-code flow service -----------------------------------------------


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
            if rec.user_id is None:
                # Sign-in flow: identity → user → session, then store bundle.
                resolved = await self._resolve_signin(bundle)
                ciphertext = encryption.encrypt(codex_oauth.bundle_to_json(bundle))
                await self.store.upsert_api_key(
                    user_id=resolved.user_id,
                    provider=CODEX,
                    encrypted_key=ciphertext,
                )
                async with rec.lock:
                    rec.status = "complete"
                    rec.email = bundle.email
                    rec.signed_in_user_id = resolved.user_id
                    rec.signed_in_user_email = resolved.user_email
                    rec.signed_in_user_role = resolved.user_role
                    rec.session = resolved.session
            else:
                # Authenticated link: bundle stays on the existing user.
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
        except (CodexOAuthError, ValueError, AuthConflictError, IdentityConflictError):
            logger.exception("Codex OAuth failed for user=%s", rec.user_id)
            async with rec.lock:
                rec.status = "error"
                rec.error = "Sign-in failed — try again."

    async def _resolve_signin(self, bundle: TokenBundle) -> _ResolvedSignin:
        """Find or create the user behind this bundle and issue a session.

        Branching mirrors GoogleOAuthService._complete_signin:
          1. existing identity by (provider='openai', sub) — sign in
          2. existing user by email — auto-link the openai identity, sign in
          3. neither — create user + identity with the OAuth sentinel hash
        """
        sub = codex_oauth.decode_user_sub(bundle.access_token)
        email = (bundle.email or "").strip().lower()
        if not email:
            raise CodexOAuthError("ChatGPT bundle missing email — cannot sign in")

        existing_via_identity = await self.store.get_user_by_identity(
            provider=OPENAI, provider_subject=sub
        )
        if existing_via_identity is not None:
            session = await issue_session(self.store, existing_via_identity.id)
            await self.store.record_security_event(
                event_type=AuditEvent.OAUTH_SIGNIN_SUCCEEDED,
                user_id=existing_via_identity.id,
                metadata={"provider": OPENAI, "via": "existing_identity"},
            )
            return _ResolvedSignin(
                user_id=existing_via_identity.id,
                user_email=existing_via_identity.email,
                user_role=existing_via_identity.role,
                session=session,
            )

        existing_via_email = await self.store.get_user_by_email(email)
        if existing_via_email is not None:
            try:
                await self.store.create_identity(
                    user_id=existing_via_email.id,
                    provider=OPENAI,
                    provider_subject=sub,
                    email=email,
                )
            except IdentityConflictError:
                logger.warning("OpenAI identity sub=%s collided after email lookup", sub)
                raise
            session = await issue_session(self.store, existing_via_email.id)
            await self.store.record_security_event(
                event_type=AuditEvent.OAUTH_LINKED,
                user_id=existing_via_email.id,
                metadata={"provider": OPENAI, "via": "auto_link_on_email_match"},
            )
            await self.store.record_security_event(
                event_type=AuditEvent.OAUTH_SIGNIN_SUCCEEDED,
                user_id=existing_via_email.id,
                metadata={"provider": OPENAI, "via": "linked"},
            )
            return _ResolvedSignin(
                user_id=existing_via_email.id,
                user_email=existing_via_email.email,
                user_role=existing_via_email.role,
                session=session,
            )

        user = await create_user_account(
            self.store,
            email=email,
            password_hash=OAUTH_ONLY_SENTINEL_HASH,
            verified=True,
            identity=IdentitySeed(
                provider=OPENAI,
                provider_subject=sub,
                email=email,
            ),
        )
        session = await issue_session(self.store, user.id)
        await self.store.record_security_event(
            event_type=AuditEvent.OAUTH_LINKED,
            user_id=user.id,
            metadata={"provider": OPENAI, "via": "signup"},
        )
        await self.store.record_security_event(
            event_type=AuditEvent.OAUTH_SIGNIN_SUCCEEDED,
            user_id=user.id,
            metadata={"provider": OPENAI, "via": "signup"},
        )
        return _ResolvedSignin(
            user_id=user.id,
            user_email=user.email,
            user_role=user.role,
            session=session,
        )

    async def start(self, *, user_id: int) -> dict:
        return await self._kick_off_device_flow(user_id=user_id)

    async def start_signin(self) -> dict:
        return await self._kick_off_device_flow(user_id=None)

    async def _kick_off_device_flow(self, *, user_id: int | None) -> dict:
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
        return {
            "pending_id": pending_id,
            "user_code": start.user_code,
            "verification_url": start.verification_url,
            "expires_in": 15 * 60,
        }

    async def status(self, *, pending_id: str, user_id: int) -> dict:
        self.pending_flows.evict_terminal_older_than(MAX_RECORD_AGE_SECONDS)
        rec = self.pending_flows.get(pending_id)
        if rec is None or rec.user_id != user_id:
            raise CodexOAuthUnknownFlowError("Unknown pending flow")
        async with rec.lock:
            snapshot = {"status": rec.status, "email": rec.email, "error": rec.error}
        if snapshot["status"] != "pending":
            self.pending_flows.pop(pending_id)
        return snapshot

    async def signin_status(self, *, pending_id: str) -> "_SigninStatusSnapshot":
        """Status endpoint for the unauthenticated sign-in flow.

        On `complete`, returns the IssuedSession + user info so the
        route can both set the auth cookie and return a `{token, user}`
        body. Pops the record on terminal status — the session is
        one-shot.
        """
        self.pending_flows.evict_terminal_older_than(MAX_RECORD_AGE_SECONDS)
        rec = self.pending_flows.get(pending_id)
        # Reject if this is an authenticated-link flow (user_id is set).
        if rec is None or rec.user_id is not None:
            raise CodexOAuthUnknownFlowError("Unknown pending flow")
        async with rec.lock:
            snapshot = _SigninStatusSnapshot(
                status=rec.status,
                email=rec.email,
                error=rec.error,
                user_id=rec.signed_in_user_id,
                user_email=rec.signed_in_user_email,
                user_role=rec.signed_in_user_role,
                session=rec.session,
            )
        if snapshot.status != "pending":
            self.pending_flows.pop(pending_id)
        return snapshot

    async def cancel(self, *, pending_id: str, user_id: int) -> None:
        rec = self.pending_flows.get(pending_id)
        if rec is None or rec.user_id != user_id:
            raise CodexOAuthUnknownFlowError("Unknown pending flow")
        await self._pop_and_cancel(pending_id, rec)

    async def cancel_signin(self, *, pending_id: str) -> None:
        rec = self.pending_flows.get(pending_id)
        if rec is None or rec.user_id is not None:
            raise CodexOAuthUnknownFlowError("Unknown pending flow")
        await self._pop_and_cancel(pending_id, rec)

    async def _pop_and_cancel(self, pending_id: str, rec) -> None:
        self.pending_flows.pop(pending_id)
        if rec.task and not rec.task.done():
            rec.task.cancel()
            try:
                await rec.task
            except asyncio.CancelledError:
                pass
