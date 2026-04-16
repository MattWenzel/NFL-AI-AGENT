"""Unverified JWT payload decode for OAuth tokens.

Codex tokens are issued by OpenAI's auth service; we only read the payload
for claims (email, chatgpt_account_id). Signature verification is handled by
the auth server, not us. See skill `codex-oauth`.
"""

from __future__ import annotations

import base64
import json


class JWTDecodeError(ValueError):
    """Raised when a token cannot be decoded."""


def decode_jwt_payload(token: str) -> dict:
    parts = token.split(".")
    if len(parts) < 2:
        raise JWTDecodeError("Token is not a JWT (missing payload segment)")
    payload_b64 = parts[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    try:
        data = base64.urlsafe_b64decode(padded)
    except (ValueError, base64.binascii.Error) as exc:
        raise JWTDecodeError(f"Malformed base64 in JWT payload: {exc}") from exc
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise JWTDecodeError(f"JWT payload is not JSON: {exc}") from exc


def email_from_id_token(id_token: str) -> str | None:
    claims = decode_jwt_payload(id_token)
    value = claims.get("email")
    return value if isinstance(value, str) and value else None


def chatgpt_account_id_from_access_token(access_token: str) -> str:
    claims = decode_jwt_payload(access_token)
    auth = claims.get("https://api.openai.com/auth")
    if not isinstance(auth, dict):
        raise JWTDecodeError("access_token missing 'https://api.openai.com/auth' claim")
    account_id = auth.get("chatgpt_account_id")
    if not isinstance(account_id, str) or not account_id:
        raise JWTDecodeError("access_token missing chatgpt_account_id")
    return account_id
