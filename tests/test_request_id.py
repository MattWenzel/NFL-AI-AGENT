"""Tests for the request-ID correlation wiring.

Two surfaces:
  - `RequestIDFilter` injects the `request_id` contextvar into log records
  - `RequestIDMiddleware` stamps every response with `X-Request-ID` and
    accepts a client-supplied value
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.server.logging import RequestIDFilter
from backend.server.middleware import RequestIDMiddleware
from backend.server.request_context import request_id


def test_filter_uses_contextvar_value():
    f = RequestIDFilter()
    record = logging.LogRecord("x", logging.INFO, "p", 1, "msg", None, None)
    token = request_id.set("abc12345")
    try:
        f.filter(record)
    finally:
        request_id.reset(token)
    assert record.request_id == "abc12345"


def test_filter_falls_back_to_default_outside_request():
    f = RequestIDFilter()
    record = logging.LogRecord("x", logging.INFO, "p", 1, "msg", None, None)
    f.filter(record)
    # The contextvar default is "-" so non-request logs render with a dash
    # instead of failing the formatter with a KeyError.
    assert record.request_id == "-"


def test_middleware_generates_id_when_none_supplied():
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/x")
    def handler():
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/x")
    assert response.status_code == 200
    rid = response.headers.get("X-Request-ID")
    assert rid is not None
    assert len(rid) == 8  # 4 bytes hex


def test_middleware_echoes_client_supplied_id():
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/x")
    def handler():
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/x", headers={"X-Request-ID": "trace-99"})
    assert response.headers["X-Request-ID"] == "trace-99"


def test_middleware_caps_oversized_id():
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/x")
    def handler():
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/x", headers={"X-Request-ID": "a" * 1000})
    # 32-char cap so a hostile client can't bloat every log line
    assert len(response.headers["X-Request-ID"]) == 32
