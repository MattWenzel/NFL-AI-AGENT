#!/usr/bin/env python3
"""CLI chat interface for the NFL stats agent."""

import asyncio
import json
import sys

from config import RUNTIME_DB_PATH, load_dotenv
from agent.providers import (
    create_client, get_provider, get_default_provider,
    provider_is_available, LLMError,
)
from agent.runtime import ChatRuntime, TOOLS
from agent.runtime_store import RuntimeStore


def _parse_args() -> tuple[str | None, str | None]:
    provider = None
    model = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--provider", "-p") and i + 1 < len(args):
            provider = args[i + 1]
            i += 2
            continue
        if arg.startswith("--provider="):
            provider = arg.split("=", 1)[1]
            i += 1
            continue
        if arg in ("--model", "-m") and i + 1 < len(args):
            model = args[i + 1]
            i += 2
            continue
        if arg.startswith("--model="):
            model = arg.split("=", 1)[1]
            i += 1
            continue
        i += 1
    return provider, model


async def run_turn(runtime: ChatRuntime, session, client, provider_name: str, user_input: str) -> None:
    streamed_text = False
    async for event in runtime.run_session(
        session,
        user_input,
        client,
        tools=TOOLS,
        provider_name=provider_name,
    ):
        if event.type == "text_delta" and event.text:
            print(event.text, end="", flush=True)
            streamed_text = True
        elif event.type == "tool_pending":
            if streamed_text:
                print()
                streamed_text = False
            print(f"\033[90m[Tool: {event.name}] {_format_input(event.input or {})}\033[0m")
        elif event.type == "tool_completed":
            preview = event.result[:200] + "..." if event.result and len(event.result) > 200 else event.result or ""
            print(f"\033[90m  -> {preview}\033[0m")
        elif event.type == "tool_failed":
            print(f"\033[31mTool failed: {event.error}\033[0m")
        elif event.type == "runtime_error":
            print(f"\033[33m{event.error}\033[0m")
    if streamed_text:
        print()


def _format_input(input_data: dict) -> str:
    if not input_data:
        return "{}"
    text = json.dumps(input_data, separators=(",", ":"))
    if len(text) > 150:
        return text[:150] + "..."
    return text


async def main():
    load_dotenv()

    provider, model = _parse_args()
    provider_name = provider or get_default_provider()

    try:
        info = get_provider(provider_name)
    except KeyError as e:
        print(f"Error: {e}")
        sys.exit(1)

    if not provider_is_available(info):
        print(f"Error: {info.env_key} environment variable is required for {info.display_name}.")
        sys.exit(1)

    try:
        client = create_client(provider=provider_name, model=model)
    except LLMError as e:
        print(f"Error: {e}")
        sys.exit(1)

    store = RuntimeStore(RUNTIME_DB_PATH)
    runtime = ChatRuntime(store)
    session = store.get_or_create_session(
        provider=provider_name,
        model=client.model,
        context_window=info.effective_context_window,
    )

    print(f"NFL Stats Chat (provider: {info.display_name}, model: {client.model})")
    print("Type your question, /history to inspect the transcript, or quit to exit.")
    print()

    while True:
        try:
            user_input = input("\033[1mYou:\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "/quit", "/exit"):
            print("Goodbye!")
            break
        if user_input == "/history":
            transcript = store.get_transcript(session.id)
            for turn in transcript.turns:
                preview = turn.text[:120].replace("\n", " ")
                compacted = " compacted" if turn.compacted else ""
                print(f"  [{turn.role}/{turn.status}{compacted}] {preview}")
            continue

        print()
        try:
            await run_turn(runtime, session, client, provider_name, user_input)
        except Exception as e:
            print(f"\033[31mError: {e}\033[0m")
        print()


if __name__ == "__main__":
    asyncio.run(main())
