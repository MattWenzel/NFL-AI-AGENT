"""On-disk storage for OAuth tokens.

Schema keyed by email to support multi-account later; a `default_email`
pointer picks the active account when callers don't specify one. Writes
are atomic via tmp-file + os.replace, mirroring the conversation store.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TokenRecord:
    access_token: str
    refresh_token: str
    expires_at_ms: int
    id_token: str | None = None
    email: str | None = None

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at_ms": self.expires_at_ms,
            "id_token": self.id_token,
            "email": self.email,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TokenRecord":
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at_ms=int(data["expires_at_ms"]),
            id_token=data.get("id_token"),
            email=data.get("email"),
        )


@dataclass
class _StoreState:
    accounts: dict[str, TokenRecord] = field(default_factory=dict)
    default_email: str | None = None


class TokenStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> _StoreState:
        if not self.path.exists():
            return _StoreState()
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read %s, treating as empty: %s", self.path, exc)
            return _StoreState()
        accounts = {
            email: TokenRecord.from_dict(record)
            for email, record in data.get("accounts", {}).items()
            if isinstance(record, dict)
        }
        return _StoreState(
            accounts=accounts,
            default_email=data.get("default_email"),
        )

    def _write(self, state: _StoreState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "accounts": {email: rec.to_dict() for email, rec in state.accounts.items()},
            "default_email": state.default_email,
        }
        fd, tmp_path = tempfile.mkstemp(dir=self.path.parent, prefix=".codex_auth_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def get(self, email: str | None = None) -> TokenRecord | None:
        state = self._load()
        key = email or state.default_email
        if not key:
            return None
        return state.accounts.get(key)

    def save(self, record: TokenRecord, *, make_default: bool = True) -> None:
        if not record.email:
            raise ValueError("TokenRecord.email is required before saving")
        state = self._load()
        state.accounts[record.email] = record
        if make_default or state.default_email is None:
            state.default_email = record.email
        self._write(state)

    def delete(self, email: str | None = None) -> bool:
        state = self._load()
        key = email or state.default_email
        if not key or key not in state.accounts:
            return False
        del state.accounts[key]
        if state.default_email == key:
            state.default_email = next(iter(state.accounts), None)
        self._write(state)
        return True

    def has_record(self, email: str | None = None) -> bool:
        return self.get(email) is not None
