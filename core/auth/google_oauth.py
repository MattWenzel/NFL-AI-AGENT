"""Google OAuth 2.0 + OpenID Connect primitives.

Thin, self-contained module — just the pieces needed for a sign-in flow:

  - `pkce_pair()`          → (verifier, challenge) per RFC 7636
  - `build_authorization_url(...)` → 302 target for `/auth/oauth/google/start`
  - `exchange_code_for_identity(...)` → POST token endpoint + verify ID token
    against Google's JWKS, return a `GoogleIdentity` dataclass.

No FastAPI or app-state imports — this module only knows about Google.
Callers plug in `client_id`, `client_secret`, `redirect_uri` from core.config.

JWKS is cached in memory for 1 hour (Google rotates on the order of weeks).
The cache is per-process; a redeploy invalidates it, which is fine.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.jwk import KeySet

from core.auth.errors import GoogleOAuthError
from core.auth.types import GoogleIdentity

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
DEFAULT_SCOPES = "openid email profile"

_JWKS_TTL_SECONDS = 3600
_jwks_cache: tuple[float, KeySet] | None = None


def pkce_pair() -> tuple[str, str]:
    """Return `(verifier, challenge)` per RFC 7636.

    Verifier: 32 random bytes, base64-urlsafe, no padding → 43 chars.
    Challenge: SHA-256(verifier), base64-urlsafe, no padding → 43 chars.
    """
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    nonce: str,
    scopes: str = DEFAULT_SCOPES,
) -> str:
    """Build the GET URL for bouncing the browser to Google's consent screen.

    `prompt=select_account` lets users switch Google accounts cleanly
    instead of silently re-using the last one. `access_type=online`
    signals we don't want a refresh token — we only use the ID token
    once, during the callback, to establish identity.
    """
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "nonce": nonce,
        "access_type": "online",
        "prompt": "select_account",
        "include_granted_scopes": "true",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_identity(
    *,
    code: str,
    code_verifier: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    expected_nonce: str,
    http_client: httpx.AsyncClient | None = None,
) -> GoogleIdentity:
    """Run the OAuth code → token exchange and verify the returned ID token.

    Returns a primitive `GoogleIdentity`. Raises `GoogleOAuthError` on
    any failure (network, non-2xx, missing id_token, signature invalid,
    claim mismatch, etc.).
    """
    close_client = False
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=10.0)
        close_client = True
    try:
        try:
            resp = await http_client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": code_verifier,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise GoogleOAuthError(f"Token endpoint unreachable: {exc}") from exc

        if resp.status_code != 200:
            # Google returns a structured error body; log the detail but
            # don't let it leak past GoogleOAuthError.
            logger.warning(
                "Google token exchange failed (status=%d): %s",
                resp.status_code,
                resp.text[:500],
            )
            raise GoogleOAuthError(f"Token exchange failed ({resp.status_code})")

        try:
            body = resp.json()
        except ValueError as exc:
            raise GoogleOAuthError("Token endpoint returned non-JSON") from exc

        id_token = body.get("id_token")
        if not id_token:
            raise GoogleOAuthError("Token response missing id_token")

        jwks = await _get_jwks(http_client)
        claims = _verify_id_token(
            id_token=id_token,
            jwks=jwks,
            expected_audience=client_id,
            expected_nonce=expected_nonce,
        )
    finally:
        if close_client:
            await http_client.aclose()

    sub = str(claims.get("sub") or "")
    email = str(claims.get("email") or "")
    if not sub or not email:
        raise GoogleOAuthError("ID token missing sub or email")
    email_verified = bool(claims.get("email_verified"))
    name = claims.get("name")
    return GoogleIdentity(
        sub=sub,
        email=email.lower(),
        email_verified=email_verified,
        name=str(name) if name else None,
    )


async def _get_jwks(http_client: httpx.AsyncClient) -> KeySet:
    """Fetch Google's JWKS, caching for `_JWKS_TTL_SECONDS`.

    Google's docs say keys rotate "from time to time" (weeks), so hourly
    refresh is conservative. If the fetch fails but we have a stale
    cached copy, return that — better than hard-failing sign-in when
    Google is briefly unreachable.
    """
    global _jwks_cache
    now = time.monotonic()
    if _jwks_cache is not None and now - _jwks_cache[0] < _JWKS_TTL_SECONDS:
        return _jwks_cache[1]
    try:
        resp = await http_client.get(GOOGLE_JWKS_URL)
        resp.raise_for_status()
        jwks = KeySet.import_key_set(resp.json())
    except (httpx.HTTPError, ValueError) as exc:
        if _jwks_cache is not None:
            logger.warning("JWKS refresh failed, using stale cache: %s", exc)
            return _jwks_cache[1]
        raise GoogleOAuthError(f"Could not fetch Google JWKS: {exc}") from exc
    _jwks_cache = (now, jwks)
    return jwks


def _verify_id_token(
    *,
    id_token: str,
    jwks: KeySet,
    expected_audience: str,
    expected_nonce: str,
) -> dict[str, Any]:
    """Decode + verify an ID token JWT. Returns the claims dict.

    Checks: signature (via JWKS), issuer, audience, nonce, expiry.
    """
    try:
        token = jwt.decode(id_token, jwks, algorithms=["RS256"])
    except Exception as exc:  # joserfc raises a family; normalize
        raise GoogleOAuthError(f"ID token signature verification failed: {exc}") from exc

    claims = dict(token.claims)
    issuer = claims.get("iss")
    if issuer not in GOOGLE_ISSUERS:
        raise GoogleOAuthError(f"ID token issuer mismatch: {issuer!r}")

    audience = claims.get("aud")
    if isinstance(audience, list):
        aud_ok = expected_audience in audience
    else:
        aud_ok = audience == expected_audience
    if not aud_ok:
        raise GoogleOAuthError("ID token audience mismatch")

    if claims.get("nonce") != expected_nonce:
        raise GoogleOAuthError("ID token nonce mismatch")

    now = int(time.time())
    exp = int(claims.get("exp") or 0)
    if exp < now:
        raise GoogleOAuthError("ID token expired")
    # `iat` is optional-to-check but worth a sanity window to catch badly-
    # skewed clocks or replayed tokens.
    iat = int(claims.get("iat") or 0)
    if iat > now + 300:
        raise GoogleOAuthError("ID token issued-at is in the future")
    return claims


def reset_jwks_cache() -> None:
    """Test helper — forces the next `_get_jwks` to fetch fresh."""
    global _jwks_cache
    _jwks_cache = None
