#!/usr/bin/env python3
"""Entry point for the nflverse API server."""

import argparse
import logging
import os
import uvicorn

from backend.config import load_dotenv


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="nflverse API server")
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG logging (tool calls, SQL, token usage, timing)",
    )
    args = parser.parse_args()

    load_dotenv()

    # Set env var so the reloaded worker process picks up the flag
    if args.verbose:
        os.environ["NFLVERSE_VERBOSE"] = "1"

    from backend.api.logging import setup_logging
    setup_logging(verbose=args.verbose)

    # Bind to loopback by default so a production deploy can't be reached
    # directly — only the reverse proxy (Caddy/nginx) can talk to the app.
    # LAN-dev use case: set HOST=0.0.0.0 in env to expose to other devices.
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8001"))

    # proxy_headers=True makes uvicorn honor X-Forwarded-For / X-Forwarded-Proto
    # from a trusted reverse proxy, so request.client.host reflects the real
    # end-user IP (critical for the per-IP rate limiter). forwarded_allow_ips
    # restricts who is trusted to set those headers — default to loopback only
    # so a directly-reachable app doesn't accept spoofed headers.
    forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")

    if host == "0.0.0.0":
        logging.getLogger(__name__).warning(
            "Binding to 0.0.0.0 — the app is reachable on every interface. "
            "For production, set HOST=127.0.0.1 and put a reverse proxy with TLS in front."
        )

    uvicorn.run(
        "backend.api.app:app",
        host=host,
        port=port,
        reload=True,
        proxy_headers=True,
        forwarded_allow_ips=forwarded_allow_ips,
    )
