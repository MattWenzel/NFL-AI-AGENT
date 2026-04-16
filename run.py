#!/usr/bin/env python3
"""Entry point for the nflverse API server."""

import argparse
import os
import uvicorn

from config import load_dotenv


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

    from agent.logger import setup_logging
    setup_logging(verbose=args.verbose)

    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
    )
