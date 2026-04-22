"""Compatibility shim composing the storage session/transcript mixins."""

from storage.session_store import SessionStoreMixin
from storage.transcript_store import TranscriptStoreMixin


class TranscriptsMixin(SessionStoreMixin, TranscriptStoreMixin):
    """Backwards-compatible storage mixin kept as a thin composition layer."""

