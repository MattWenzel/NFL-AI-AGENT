from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
root_str = str(ROOT)
if root_str not in sys.path:
    sys.path.insert(0, root_str)


@pytest.fixture
def no_login_throttle(monkeypatch):
    """Skip the per-email progressive delay during login.

    Production code adds an `asyncio.sleep` of up to 4s per failed login
    (see backend/services/auth/service.py::_progressive_delay) to make
    password spraying uneconomic. Rate-limit and lockout tests assert the
    cap-flip behavior, not the delay timing — without this patch one such
    test alone burned ~24s of every test run.
    """
    async def _no_sleep(*_args, **_kwargs):
        return None
    monkeypatch.setattr(
        "backend.application.auth.service.asyncio.sleep", _no_sleep
    )
