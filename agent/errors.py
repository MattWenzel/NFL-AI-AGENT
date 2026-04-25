"""Exceptions raised by the chat runtime loop.

Kept separate from `events.py` (which holds the event variants streamed
to consumers) so that "errors the loop can raise" and "events the loop
emits" live in clearly separated files.
"""


class RuntimeLoopError(Exception):
    """Raised when the runtime detects an unrecoverable loop condition."""
