from __future__ import annotations

from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.process_state import AppProcessState
from server.repositories import RepositoryBundle
from agent.runtime_repositories import RuntimeRepositoryBundle


def build_test_app(*, runtime_store=None) -> FastAPI:
    app = FastAPI()
    if runtime_store is not None:
        app.state.runtime_store = runtime_store
        app.state.repositories = RepositoryBundle.from_store(runtime_store)
        app.state.runtime_repositories = RuntimeRepositoryBundle.from_store(runtime_store)
    app.state.process_state = AppProcessState()
    return app


@contextmanager
def managed_test_client(app: FastAPI):
    with TestClient(app) as client:
        yield client
