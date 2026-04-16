"""PKCE + state generation for OAuth authorization code flow."""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class PKCEChallenge:
    verifier: str
    challenge: str
    state: str


def _b64url_nopad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> PKCEChallenge:
    verifier = _b64url_nopad(os.urandom(64))
    challenge = _b64url_nopad(hashlib.sha256(verifier.encode("ascii")).digest())
    state = _b64url_nopad(os.urandom(32))
    return PKCEChallenge(verifier=verifier, challenge=challenge, state=state)
