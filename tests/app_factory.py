from __future__ import annotations

from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.lib.agent.runtime import ChatRuntime
from backend.server.process_state import AppProcessState


def build_test_app(*, runtime_store=None) -> FastAPI:
    app = FastAPI()
    if runtime_store is not None:
        app.state.store = runtime_store
        app.state.chat_runtime = ChatRuntime(runtime_store)
    app.state.process_state = AppProcessState()
    return app


@contextmanager
def managed_test_client(app: FastAPI):
    with TestClient(app) as client:
        yield client
