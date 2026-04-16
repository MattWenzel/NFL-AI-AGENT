"""Centralized logging setup. Call setup_logging() once from run.py."""

import logging
import sys


def setup_logging(verbose: bool = False) -> None:
    """Configure root logger.

    Default (verbose=False): WARNING+ only — you see errors and nothing else.
    --verbose: DEBUG level — tool calls, SQL, token usage, timing.
    Console output only. Pipe to file if needed:
        python3 run.py --verbose 2>&1 | tee debug.log
    """
    level = logging.DEBUG if verbose else logging.WARNING
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S")
    )
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # Quiet noisy third-party loggers even in verbose mode
    for noisy in ("uvicorn.access", "anthropic", "anthropic._base_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
