"""Provider-level smoke tests for tool_choice wiring.

Each provider translates our canonical `ToolChoice` literal
("auto" / "required" / "none") into its own SDK wire format. These tests
verify the string lands in the right shape on each provider's kwargs/body
without actually calling the SDK.
"""

from __future__ import annotations

from backend.domain.providers.clients.anthropic import AnthropicClient, _ANTHROPIC_TOOL_CHOICE
from backend.domain.providers.clients.codex import OpenAICodexClient
from backend.domain.providers.clients.openai import OpenAIClient


def _anthropic_kwargs(tool_choice):
    # Dodge the network-client constructor — we only need _build_kwargs.
    client = AnthropicClient.__new__(AnthropicClient)
    client.model = "claude-sonnet-4-6"
    client.max_output_tokens = 1024
    return client._build_kwargs(
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        system=None,
        tool_choice=tool_choice,
    )


def _openai_kwargs(tool_choice):
    client = OpenAIClient.__new__(OpenAIClient)
    client.model = "gpt-5"
    client.max_output_tokens = 1024
    return client._build_kwargs(
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        system=None,
        tool_choice=tool_choice,
    )


def _codex_body(tool_choice):
    client = OpenAICodexClient.__new__(OpenAICodexClient)
    client.model = "gpt-5.3-codex"
    client.max_output_tokens = 1024
    return client._build_body(
        messages=[],
        tools=None,
        system=None,
        tool_choice=tool_choice,
    )


def test_anthropic_maps_required_to_any():
    kwargs = _anthropic_kwargs("required")
    assert kwargs["tool_choice"] == {"type": "any"}


def test_anthropic_maps_auto_and_none():
    assert _anthropic_kwargs("auto")["tool_choice"] == {"type": "auto"}
    assert _anthropic_kwargs("none")["tool_choice"] == {"type": "none"}


def test_anthropic_omits_tool_choice_when_unset():
    """None means 'use the SDK default' — don't send the key at all."""
    kwargs = _anthropic_kwargs(None)
    assert "tool_choice" not in kwargs


def test_anthropic_lookup_table_covers_all_literals():
    """Guards against typos in the dict: every canonical value maps."""
    assert set(_ANTHROPIC_TOOL_CHOICE.keys()) == {"auto", "required", "none"}


def test_openai_passes_string_verbatim():
    for value in ("auto", "required", "none"):
        assert _openai_kwargs(value)["tool_choice"] == value


def test_openai_omits_tool_choice_when_unset():
    assert "tool_choice" not in _openai_kwargs(None)


def test_codex_passes_string_and_defaults_to_auto():
    """Codex body always includes tool_choice — required by the Responses API."""
    assert _codex_body("required")["tool_choice"] == "required"
    assert _codex_body("none")["tool_choice"] == "none"
    assert _codex_body(None)["tool_choice"] == "auto"
