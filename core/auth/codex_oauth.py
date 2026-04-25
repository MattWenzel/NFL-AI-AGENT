"""Device-code OAuth helpers for OpenAI's Codex (ChatGPT) backend.

Pure functions, no DB coupling. Shared between the server OAuth router and
any CLI/smoke-test helpers.

Wire contract is reverse-engineered from `openai/codex:codex-rs/login/src/
device_code_auth.rs`. Device-code flow bypasses the localhost-pinned
redirect URI used by the standard authorization-code flow, which is why
this module exists instead of a plain /oauth/authorize redirect.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from urllib.parse import urlencode

import httpx

from core.auth.errors import CodexOAuthError, DeviceCodeExpired
from core.auth.types import DeviceCodeAuthorized, DeviceCodeStart, TokenBundle

logger = logging.getLogger(__name__)


# Public Codex CLI client — the only client ID OpenAI's auth server
# accepts for ChatGPT-backed API access. Not a secret.
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ISSUER = "https://auth.openai.com"
VERIFICATION_URL = f"{ISSUER}/codex/device"
DEVICE_REDIRECT_URI = f"{ISSUER}/deviceauth/callback"

# Safety margin for expiry checks — refresh when the token has less than
# this many seconds of life left so in-flight requests don't race the
# expiration boundary.
REFRESH_SKEW_SECONDS = 30


# ---------------- low-level JWT decode ----------------


def _decode_jwt_payload(token: str) -> dict:
    """Split a JWT, base64url-decode the payload segment, return the parsed JSON.

    Signature verification is not needed — we only use this to extract
    claims that the token already authenticates us to use (account id,
    email, expiry). If the token is forged, the downstream API call
    fails on its own.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise CodexOAuthError("JWT has wrong number of segments")
        payload_b64 = parts[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        raw = base64.urlsafe_b64decode(padded)
        return json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise CodexOAuthError(f"malformed JWT: {exc}") from exc


def decode_account_id(access_token: str) -> str:
    """Pull `chatgpt_account_id` out of the access_token's JWT payload.

    This value is required as the `chatgpt-account-id` header on every
    Codex API request. Fail fast if missing — there's no fallback.
    """
    claims = _decode_jwt_payload(access_token)
    auth_claims = claims.get("https://api.openai.com/auth") or {}
    account_id = auth_claims.get("chatgpt_account_id")
    if not account_id:
        raise CodexOAuthError(
            "access token missing chatgpt_account_id — re-run the Connect ChatGPT flow"
        )
    return account_id


def decode_email(id_token: str) -> str | None:
    """Best-effort email extraction from the id_token. Returns None if absent."""
    try:
        claims = _decode_jwt_payload(id_token)
    except CodexOAuthError:
        return None
    email = claims.get("email")
    return email if isinstance(email, str) else None


def _compute_expires_at(access_token: str, expires_in: int | None) -> int:
    """Derive epoch-ms expiry. Prefer the JWT `exp` claim; fall back to expires_in."""
    try:
        claims = _decode_jwt_payload(access_token)
        exp = claims.get("exp")
        if isinstance(exp, (int, float)) and exp > 0:
            return int(exp * 1000)
    except CodexOAuthError:
        pass
    # Fallback: expires_in is seconds from now. Default 55 minutes if missing.
    seconds = expires_in if isinstance(expires_in, (int, float)) and expires_in > 0 else 55 * 60
    return int(time.time() * 1000) + int(seconds) * 1000


# ---------------- device-code flow ----------------


async def request_device_code(
    client_id: str = CODEX_CLIENT_ID,
    *,
    client: httpx.AsyncClient | None = None,
) -> DeviceCodeStart:
    """Request a user_code + device_auth_id from OpenAI.

    Returns a DeviceCodeStart with everything the UI needs to display
    the code + verification link, plus the internal device_auth_id
    needed to poll for the authorization grant.
    """
    url = f"{ISSUER}/api/accounts/deviceauth/usercode"
    payload = {"client_id": client_id}
    owns_client = client is None
    cli = client or httpx.AsyncClient(timeout=30.0)
    try:
        resp = await cli.post(url, json=payload)
    finally:
        if owns_client:
            await cli.aclose()
    if resp.status_code == 404:
        raise CodexOAuthError(
            "device-code endpoint not reachable — OpenAI may have changed the API"
        )
    if resp.status_code >= 400:
        raise CodexOAuthError(
            f"device-code request failed: HTTP {resp.status_code} {resp.text[:200]}"
        )
    data = resp.json()
    interval_raw = data.get("interval", 5)
    # Server sometimes returns interval as a string.
    interval = int(str(interval_raw).strip()) if interval_raw else 5
    return DeviceCodeStart(
        device_auth_id=data["device_auth_id"],
        user_code=data.get("user_code") or data.get("usercode") or "",
        interval=interval,
        verification_url=VERIFICATION_URL,
    )


async def poll_device_code(
    device_auth_id: str,
    user_code: str,
    *,
    interval_seconds: int = 5,
    max_wait_seconds: int = 15 * 60,
    client: httpx.AsyncClient | None = None,
    sleep=None,
) -> DeviceCodeAuthorized:
    """Poll until the user completes sign-in, the window expires, or an error occurs.

    403/404 are treated as "still waiting" and loop back after sleeping for
    `interval_seconds`. 2xx surfaces the server-generated authorization_code
    and PKCE verifier. `sleep` is injected for tests.
    """
    import asyncio

    sleep = sleep or asyncio.sleep
    url = f"{ISSUER}/api/accounts/deviceauth/token"
    payload = {"device_auth_id": device_auth_id, "user_code": user_code}
    owns_client = client is None
    cli = client or httpx.AsyncClient(timeout=30.0)
    start = time.monotonic()
    try:
        while True:
            resp = await cli.post(url, json=payload)
            if resp.status_code < 300:
                data = resp.json()
                return DeviceCodeAuthorized(
                    authorization_code=data["authorization_code"],
                    code_verifier=data["code_verifier"],
                    code_challenge=data.get("code_challenge", ""),
                )
            if resp.status_code in (403, 404):
                # User hasn't completed the sign-in yet — sleep and retry.
                elapsed = time.monotonic() - start
                if elapsed >= max_wait_seconds:
                    raise DeviceCodeExpired(
                        "device-code sign-in timed out after 15 minutes"
                    )
                remaining = max_wait_seconds - elapsed
                await sleep(min(interval_seconds, remaining))
                continue
            raise CodexOAuthError(
                f"device-code poll failed: HTTP {resp.status_code} {resp.text[:200]}"
            )
    finally:
        if owns_client:
            await cli.aclose()


async def exchange_code(
    authorization_code: str,
    code_verifier: str,
    *,
    client_id: str = CODEX_CLIENT_ID,
    redirect_uri: str = DEVICE_REDIRECT_URI,
    client: httpx.AsyncClient | None = None,
) -> TokenBundle:
    """Exchange a device-code-issued authorization_code for tokens."""
    url = f"{ISSUER}/oauth/token"
    body = urlencode({
        "grant_type": "authorization_code",
        "code": authorization_code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    })
    return await _post_token_endpoint(url, body, client)


async def refresh_access_token(
    refresh_token: str,
    *,
    client_id: str = CODEX_CLIENT_ID,
    client: httpx.AsyncClient | None = None,
) -> TokenBundle:
    """Rotate the token pair. Caller should replace its stored bundle with
    the return value — OpenAI may return a new refresh_token, in which case
    the old one is no longer valid."""
    url = f"{ISSUER}/oauth/token"
    body = urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    })
    bundle = await _post_token_endpoint(url, body, client, prior_refresh_token=refresh_token)
    return bundle


async def _post_token_endpoint(
    url: str,
    body: str,
    client: httpx.AsyncClient | None,
    *,
    prior_refresh_token: str | None = None,
) -> TokenBundle:
    owns_client = client is None
    cli = client or httpx.AsyncClient(timeout=30.0)
    try:
        resp = await cli.post(
            url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            content=body,
        )
    finally:
        if owns_client:
            await cli.aclose()
    if resp.status_code >= 400:
        raise CodexOAuthError(
            f"token endpoint failed: HTTP {resp.status_code} {resp.text[:200]}"
        )
    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        raise CodexOAuthError("token endpoint response missing access_token")
    # Refresh may or may not rotate — fall back to the old one if the server omits it.
    refresh_token = data.get("refresh_token") or prior_refresh_token
    if not refresh_token:
        raise CodexOAuthError("token endpoint response missing refresh_token")
    id_token = data.get("id_token") or ""
    email = decode_email(id_token) if id_token else None
    expires_at = _compute_expires_at(access_token, data.get("expires_in"))
    return TokenBundle(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        email=email,
    )


# ---------------- storage helpers (used by the router + dependency) ----------------


def bundle_to_json(bundle: TokenBundle) -> str:
    return json.dumps({
        "access_token": bundle.access_token,
        "refresh_token": bundle.refresh_token,
        "expires_at": bundle.expires_at,
        "email": bundle.email,
    })


def bundle_from_json(raw: str) -> TokenBundle:
    data = json.loads(raw)
    return TokenBundle(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at=int(data["expires_at"]),
        email=data.get("email"),
    )


def is_near_expiry(bundle: TokenBundle, *, skew_seconds: int = REFRESH_SKEW_SECONDS) -> bool:
    now_ms = int(time.time() * 1000)
    return bundle.expires_at - now_ms < skew_seconds * 1000
