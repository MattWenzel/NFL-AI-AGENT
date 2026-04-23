"""Centralized logging setup. Call setup_logging() once from run.py."""

import logging
import re
import sys


# Patterns that look like secrets — applied to every log record by the
# SecretRedactingFilter below. The goal is defense-in-depth: if a caller
# ever passes a raw key into a log message (e.g. a user pasting it into a
# chat turn), the string never hits stdout or log aggregators in the clear.
_SECRET_PATTERNS = [
    # Anthropic keys
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    # OpenAI keys (project + legacy)
    re.compile(r"sk-proj-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    # Bearer tokens
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}"),
    # JWTs
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
]


def _redact(value: str) -> str:
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value


class SecretRedactingFilter(logging.Filter):
    """Mask secret-shaped substrings in log records before they're emitted.

    Applies to both the pre-formatted `msg` and the `args` so formatters that
    join them later (e.g. `logger.info("got %s", key)`) still redact. Non-string
    args are passed through untouched.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 — logging API name
        if isinstance(record.msg, str):
            record.msg = _redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: (_redact(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    _redact(a) if isinstance(a, str) else a for a in record.args
                )
        return True


def setup_logging(verbose: bool = False) -> None:
    """Configure root logger.

    Default (verbose=False): WARNING+ only — you see errors and nothing else.
    --verbose: DEBUG level — tool calls, SQL, token usage, timing.
    Console output only. Pipe to file if needed:
        python3 run.py --verbose 2>&1 | tee debug.log

    A SecretRedactingFilter is always attached (even in production) so
    api-key-shaped substrings never reach stdout or log aggregators.
    """
    level = logging.DEBUG if verbose else logging.WARNING
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S")
    )
    handler.addFilter(SecretRedactingFilter())
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # Quiet noisy third-party loggers even in verbose mode
    for noisy in ("uvicorn.access", "anthropic", "anthropic._base_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
