"""OAuth orchestrator for the Codex provider.

Builds the authorize URL, exchanges codes for tokens, refreshes tokens
proactively, and exposes a single `get_valid_token()` entry point that
callers use on every request.
"""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.parse
from dataclasses import dataclass

import httpx

from agent.oauth.jwt_decode import (
    JWTDecodeError,
    chatgpt_account_id_from_access_token,
    email_from_id_token,
)
from agent.oauth.pkce import PKCEChallenge, generate_pkce
from agent.oauth.token_store import TokenRecord, TokenStore

logger = logging.getLogger(__name__)


# Public Codex CLI OAuth client + endpoints (per the codex-oauth skill).
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_REDIRECT_URI = "http://localhost:1455/auth/callback"
CODEX_SCOPE = "openid profile email offline_access"

REFRESH_SKEW_SECONDS = 30


class CodexAuthError(Exception):
    """Raised for OAuth failures (network, malformed response, refresh rejected)."""


@dataclass
class AuthorizeRequest:
    url: str
    state: str
    verifier: str


class CodexAuth:
    def __init__(self, store: TokenStore, *, http_client: httpx.AsyncClient | None = None):
        self.store = store
        self._http = http_client
        self._refresh_lock = asyncio.Lock()

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def build_authorize_request(self) -> AuthorizeRequest:
        pkce = generate_pkce()
        params = {
            "response_type": "code",
            "client_id": CODEX_CLIENT_ID,
            "redirect_uri": CODEX_REDIRECT_URI,
            "scope": CODEX_SCOPE,
            "state": pkce.state,
            "code_challenge": pkce.challenge,
            "code_challenge_method": "S256",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "pi",
        }
        url = CODEX_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)
        return AuthorizeRequest(url=url, state=pkce.state, verifier=pkce.verifier)

    async def exchange_code(self, code: str, verifier: str) -> TokenRecord:
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": CODEX_CLIENT_ID,
            "redirect_uri": CODEX_REDIRECT_URI,
            "code_verifier": verifier,
        }
        data = await self._post_token(payload)
        record = self._record_from_response(data)
        if not record.email:
            raise CodexAuthError("OAuth response did not include a usable email address")
        if not record.refresh_token:
            raise CodexAuthError("OAuth response missing refresh_token on initial exchange")
        self.store.save(record)
        logger.info("Codex OAuth: exchanged code for tokens (email=%s)", record.email)
        return record

    async def refresh(self, record: TokenRecord) -> TokenRecord:
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": record.refresh_token,
            "client_id": CODEX_CLIENT_ID,
        }
        data = await self._post_token(payload)
        refreshed = self._record_from_response(data, fallback_email=record.email)
        # Refresh responses don't always include a new refresh_token; keep the old one if missing.
        if not refreshed.refresh_token:
            refreshed.refresh_token = record.refresh_token
        self.store.save(refreshed)
        logger.info("Codex OAuth: refreshed tokens (email=%s)", refreshed.email)
        return refreshed

    async def get_valid_token(self, email: str | None = None) -> tuple[str, str]:
        """Return (access_token, chatgpt_account_id), refreshing if near expiry."""
        record = self.store.get(email)
        if record is None:
            raise CodexAuthError("Not authenticated — run the Codex login flow first.")
        if self._needs_refresh(record):
            async with self._refresh_lock:
                # Re-read under the lock; a concurrent caller may have already
                # refreshed, or the user may have logged out via /logout while
                # we were contending on the lock. In the latter case the `or
                # record` fallback would resurrect the deleted tokens, so we
                # fail loudly instead.
                fresh = self.store.get(email)
                if fresh is None:
                    raise CodexAuthError("Account was signed out during request")
                record = fresh
                if self._needs_refresh(record):
                    record = await self.refresh(record)
        try:
            account_id = chatgpt_account_id_from_access_token(record.access_token)
        except JWTDecodeError as exc:
            # The stored access_token is unreadable (e.g. corrupted during a
            # partial write) but `refresh_token` is likely still valid. Try a
            # single refresh under the lock before giving up so the user isn't
            # stuck re-authenticating for a transient on-disk issue.
            logger.warning("access_token unreadable, attempting one-shot refresh: %s", exc)
            async with self._refresh_lock:
                fresh = self.store.get(email)
                if fresh is None:
                    raise CodexAuthError("Account was signed out during request") from exc
                record = await self.refresh(fresh)
            try:
                account_id = chatgpt_account_id_from_access_token(record.access_token)
            except JWTDecodeError as retry_exc:
                raise CodexAuthError(
                    f"Cannot read chatgpt_account_id from access_token after refresh: {retry_exc}"
                ) from retry_exc
        return record.access_token, account_id

    @staticmethod
    def _needs_refresh(record: TokenRecord) -> bool:
        now_ms = int(time.time() * 1000)
        return now_ms >= record.expires_at_ms - REFRESH_SKEW_SECONDS * 1000

    async def _post_token(self, payload: dict) -> dict:
        client = await self._client()
        try:
            resp = await client.post(
                CODEX_TOKEN_URL,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.RequestError as exc:
            raise CodexAuthError(f"Network error contacting OAuth token endpoint: {exc}") from exc
        if resp.status_code >= 400:
            raise CodexAuthError(
                f"OAuth token endpoint returned {resp.status_code}: {resp.text[:400]}"
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise CodexAuthError(f"OAuth token response is not JSON: {exc}") from exc

    @staticmethod
    def _record_from_response(data: dict, *, fallback_email: str | None = None) -> TokenRecord:
        access_token = data.get("access_token")
        if not access_token:
            raise CodexAuthError("OAuth response missing access_token")
        expires_in = int(data.get("expires_in") or 0)
        expires_at_ms = int(time.time() * 1000) + expires_in * 1000
        id_token = data.get("id_token")
        email = fallback_email
        if id_token:
            try:
                email = email_from_id_token(id_token) or fallback_email
            except JWTDecodeError as exc:
                logger.warning("Could not decode id_token for email: %s", exc)
        return TokenRecord(
            access_token=access_token,
            refresh_token=data.get("refresh_token", ""),
            expires_at_ms=expires_at_ms,
            id_token=id_token,
            email=email,
        )
