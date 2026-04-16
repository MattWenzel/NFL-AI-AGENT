"""One-shot loopback HTTP server that captures an OAuth callback.

The public Codex CLI OAuth client is registered against
`http://localhost:1455/auth/callback`. We stand up a short-lived server
on that port, capture the first callback request, and shut down.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

DEFAULT_PORT = 1455
DEFAULT_PATH = "/auth/callback"


@dataclass
class CallbackResult:
    code: str
    state: str


class LoopbackCaptureError(Exception):
    """Raised when the loopback server times out or receives an error callback."""


class _CaptureHandler(BaseHTTPRequestHandler):
    expected_path: str = DEFAULT_PATH

    # The server attaches these before the thread starts.
    _loop: asyncio.AbstractEventLoop = None  # type: ignore[assignment]
    _future: asyncio.Future = None  # type: ignore[assignment]

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        logger.debug("loopback callback: " + format, *args)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != self.expected_path:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
            return
        query = parse_qs(parsed.query)
        error = query.get("error", [None])[0]
        code = query.get("code", [None])[0]
        state = query.get("state", [None])[0]
        if error:
            self._resolve_exception(LoopbackCaptureError(f"OAuth error: {error}"))
            self._respond_html(f"<h1>Sign-in failed</h1><p>{error}</p>", status=400)
            return
        if not code or not state:
            self._resolve_exception(LoopbackCaptureError("Callback missing code or state"))
            self._respond_html("<h1>Sign-in failed</h1><p>Missing code or state.</p>", status=400)
            return
        self._resolve_result(CallbackResult(code=code, state=state))
        self._respond_html(
            "<h1>Signed in</h1><p>You can close this tab and return to the terminal.</p>"
        )

    def _resolve_result(self, result: CallbackResult) -> None:
        if self._future and not self._future.done():
            self._loop.call_soon_threadsafe(self._future.set_result, result)

    def _resolve_exception(self, exc: Exception) -> None:
        if self._future and not self._future.done():
            self._loop.call_soon_threadsafe(self._future.set_exception, exc)

    def _respond_html(self, body: str, status: int = 200) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


async def run_loopback_capture(
    *,
    port: int = DEFAULT_PORT,
    path: str = DEFAULT_PATH,
    timeout_seconds: float = 300.0,
) -> CallbackResult:
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    handler_cls = type("_BoundCaptureHandler", (_CaptureHandler,), {
        "expected_path": path,
        "_loop": loop,
        "_future": future,
    })
    server = HTTPServer(("127.0.0.1", port), handler_cls)

    thread = Thread(target=server.serve_forever, name="codex-oauth-callback", daemon=True)
    thread.start()
    logger.info("OAuth loopback listening on http://127.0.0.1:%d%s", port, path)
    try:
        return await asyncio.wait_for(future, timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        raise LoopbackCaptureError(
            f"Timed out waiting for OAuth callback after {timeout_seconds:.0f}s"
        ) from exc
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
