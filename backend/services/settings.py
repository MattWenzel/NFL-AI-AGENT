"""Settings process: schemas, errors, and application service."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field

from backend.lib.db import AuditEvent, RuntimeStore
from backend.lib.providers import ProviderInfo, get_provider, list_providers
from backend.lib.auth import codex_oauth, encryption

logger = logging.getLogger(__name__)


class SettingsServiceError(Exception):
    pass


class SettingsNotFoundError(SettingsServiceError):
    pass


class ApiKeyStatus(BaseModel):
    provider: str
    display_name: str
    has_key: bool
    updated_at: str | None = None
    # OAuth-only fields — populated for providers with credential_shape="codex_oauth".
    # `email` surfaces which ChatGPT account is linked; `credential_shape` lets the
    # Settings UI pick the right input type without hard-coding provider names.
    credential_shape: str = "api_key"
    email: str | None = None
    expires_at: int | None = Field(None, description="Epoch ms; OAuth tokens only")


class ApiKeyUpdate(BaseModel):
    api_key: str | None = Field(None, description="Plaintext key to store (null to delete)")


class IdentitySummaryResponse(BaseModel):
    """Per-identity row shown in Settings -> Account -> Linked identities."""

    provider: str
    display: str
    linked_at: str
    removable: bool


class LinkGoogleStartResponse(BaseModel):
    """Response from POST /settings/identities/google/link."""

    auth_url: str


@dataclass
class SettingsService:
    store: RuntimeStore

    def _provider_info(self, provider: str) -> ProviderInfo:
        try:
            return get_provider(provider)
        except KeyError:
            raise SettingsNotFoundError(f"Unknown provider '{provider}'")

    def _build_status(self, info: ProviderInfo, rec) -> ApiKeyStatus:
        if info.credential_shape == "codex_oauth" and rec is not None:
            email = None
            expires_at = None
            try:
                plaintext = encryption.decrypt(rec.encrypted_key)
                bundle = codex_oauth.bundle_from_json(plaintext)
                email = bundle.email
                expires_at = bundle.expires_at
            except (ValueError, KeyError, json.JSONDecodeError) as exc:
                logger.warning("Could not decode Codex bundle for user=%d: %s", rec.user_id, exc)
            return ApiKeyStatus(
                provider=info.name,
                display_name=info.display_name,
                has_key=True,
                updated_at=rec.updated_at,
                credential_shape=info.credential_shape,
                email=email,
                expires_at=expires_at,
            )
        return ApiKeyStatus(
            provider=info.name,
            display_name=info.display_name,
            has_key=rec is not None,
            updated_at=rec.updated_at if rec else None,
            credential_shape=info.credential_shape,
        )

    async def list_api_key_status(self, user_id: int) -> list[ApiKeyStatus]:
        existing = {rec.provider: rec for rec in await self.store.list_api_keys(user_id)}
        return [self._build_status(info, existing.get(info.name)) for info in list_providers()]

    async def update_api_key(
        self,
        *,
        user_id: int,
        provider: str,
        api_key: str | None,
        audit_ip: str | None = None,
        audit_user_agent: str | None = None,
    ) -> ApiKeyStatus:
        info = self._provider_info(provider)
        raw = (api_key or "").strip() if api_key is not None else None
        if info.credential_shape == "codex_oauth" and raw:
            raise SettingsServiceError("Codex uses OAuth — use POST /settings/oauth/codex/start to connect.")
        if raw is None or raw == "":
            removed = await self.store.delete_api_key(user_id=user_id, provider=provider)
            if removed:
                event_type = (
                    AuditEvent.OAUTH_UNLINKED
                    if info.credential_shape == "codex_oauth"
                    else AuditEvent.API_KEY_CLEARED
                )
                await self.store.record_security_event(
                    event_type=event_type,
                    user_id=user_id,
                    ip=audit_ip,
                    user_agent=audit_user_agent,
                    metadata={"provider": provider},
                )
            return self._build_status(info, None)
        rec = await self.store.upsert_api_key(
            user_id=user_id,
            provider=provider,
            encrypted_key=encryption.encrypt(raw),
        )
        await self.store.record_security_event(
            event_type=AuditEvent.API_KEY_SET,
            user_id=user_id,
            ip=audit_ip,
            user_agent=audit_user_agent,
            metadata={"provider": provider},
        )
        return self._build_status(info, rec)
