"""Conversation compaction.

When the active transcript grows past a session's context budget, this
package decides which old turns to drop, summarizes them, and rewrites
the transcript so subsequent provider calls fit. The pieces:

- `policy`         — selector + RetentionPolicy + the orchestrator
                     `compact_if_needed`.
- `summarizer`     — LLM-backed compression of the dropped turns.
- `token_counting` — tiktoken-backed estimator used by both.

Public surface is just `compact_if_needed`; everything else is internal
to this package.
"""

from backend.lib.agent.compaction.policy import compact_if_needed

__all__ = ["compact_if_needed"]
