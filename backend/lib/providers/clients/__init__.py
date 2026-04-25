"""Concrete LLM client adapters.

Each module here implements `provider.base.BaseLLMClient` for one
upstream — Anthropic, OpenAI, or OpenAI Codex (ChatGPT-OAuth backend).
The registry in `provider/__init__.py` imports these lazily inside
`ensure_builtin_providers_registered` so optional SDKs (e.g. `openai`)
can be missing without crashing the rest of the package.
"""
