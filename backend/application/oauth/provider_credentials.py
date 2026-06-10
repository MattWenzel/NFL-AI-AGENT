"""Credential resolution services for provider-backed requests."""

from __future__ import annotations

from dataclasses import dataclass

from backend.domain.auth import encryption
from backend.application.oauth.codex import (
    CodexCredentialError,
    resolve_access_token as resolve_codex_access_token,
)
from backend.domain.providers import (
    BaseLLMClient,
    ProviderInfo,
    create_client,
    env_fallback_allowed,
    get_default_provider,
    get_provider,
    provider_is_available,
)
from backend.domain.providers.errors import LLMError
from backend.data import RuntimeStore
from backend.runtime_state import PerUserLockRegistry


class CredentialServiceError(Exception):
    """Credential lookup, refresh, or client construction failed.

    Raised with a user-facing message — callers map it to their own
    configuration error type (e.g. `ChatConfigurationError`).
    """


@dataclass
class ResolvedProviderClient:
    """Output of `resolve_provider_client` — an LLM client plus the
    resolved provider info, ready for the caller to drive."""

    client: BaseLLMClient
    info: ProviderInfo
    provider_name: str


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

    async def resolve_provider_client(
        self,
        *,
        user_id: int,
        role: str,
        provider: str | None,
        model: str | None,
    ) -> ResolvedProviderClient:
        """Default-fallback → registry lookup → key fetch → availability
        check → `create_client`, in one step.

        The dance was duplicated in `ChatService.prepare_chat` and
        `DbHelperChatService` — keep it here so a change to credential
        rules (codex_oauth, env-var fallbacks, etc.) only updates one
        path. Any failure surfaces as `CredentialServiceError`.

        The server's env-var keys are only used for users allowed by
        `SHARED_PROVIDER_KEYS` (default: admins). Everyone else must have
        stored their own key — `create_client` below would otherwise
        silently fall back to the env key and bill the operator.
        """
        provider_name = provider or get_default_provider()
        try:
            info = get_provider(provider_name)
        except KeyError as exc:
            raise CredentialServiceError(str(exc)) from exc

        user_key = await self.get_api_key(
            user_id=user_id, provider_name=provider_name
        )

        env_key_usable = env_fallback_allowed(role) and provider_is_available(info)
        if not user_key and not env_key_usable:
            if info.credential_shape == "codex_oauth":
                raise CredentialServiceError(
                    f"{info.display_name} not connected — click Connect ChatGPT in Settings."
                )
            raise CredentialServiceError(
                f"No API key for {info.display_name} — add one in Settings."
            )

        try:
            client = create_client(
                provider=provider_name, model=model, api_key=user_key
            )
        except LLMError as exc:
            raise CredentialServiceError(str(exc)) from exc

        return ResolvedProviderClient(
            client=client, info=info, provider_name=provider_name
        )
