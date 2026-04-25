"""Credential resolution services for provider-backed requests."""

from __future__ import annotations

from dataclasses import dataclass

from backend.security import encryption
from backend.processes.oauth.codex.credentials import (
    resolve_access_token as resolve_codex_access_token,
)
from backend.providers import get_provider
from backend.api.process_state import PerUserLockRegistry
from backend.processes.oauth.codex.errors import CodexCredentialError
from backend.processes.oauth.errors import CredentialServiceError
from backend.persistence import RuntimeStore


@dataclass
class ProviderCredentialService:
    store: RuntimeStore
    refresh_locks: PerUserLockRegistry

    async def get_api_key(
        self,
        *,
        user_id: int,
        provider_name: str,
    ) -> str | None:
        try:
            info = get_provider(provider_name)
        except KeyError:
            return None
        if info.credential_shape == "codex_oauth":
            try:
                return await resolve_codex_access_token(
                    self.store,
                    user_id,
                    provider_name,
                    refresh_locks=self.refresh_locks,
                )
            except CodexCredentialError as exc:
                raise CredentialServiceError(str(exc)) from exc
        rec = await self.store.get_api_key(user_id=user_id, provider=provider_name)
        if rec is None:
            return None
        try:
            return encryption.decrypt(rec.encrypted_key)
        except ValueError:
            return None
