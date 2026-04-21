"""Live end-to-end smoke: real provider → agent_loop → event stream.

Proves the full pipeline works with an actual LLM call (the rest of the
test suite stubs providers). Uses the cheapest-available model and a
trivial prompt to keep the probe under a penny.

Run::

    PYTHONIOENCODING=utf-8 python tools/live_smoke.py openrouter anthropic/claude-haiku-4.5

Env fallbacks: ``$NELLIE_SMOKE_PROVIDER``, ``$NELLIE_SMOKE_MODEL``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from karna.agents.loop import agent_loop
from karna.models import Conversation, Message
from karna.providers import get_provider, resolve_model


async def main() -> int:
    prov_name = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("NELLIE_SMOKE_PROVIDER", "openrouter")
    model_spec = (
        sys.argv[2] if len(sys.argv) > 2 else os.environ.get("NELLIE_SMOKE_MODEL", "anthropic/claude-haiku-4.5")
    )
    prompt = "Reply with exactly three words: Nellie is working."

    print(f"provider: {prov_name}")
    print(f"model:    {model_spec}")
    print(f"prompt:   {prompt!r}")
    print("-" * 72)

    # Resolve provider spec — e.g. "openrouter:anthropic/claude-haiku-4.5"
    spec = f"{prov_name}:{model_spec}" if ":" not in model_spec else model_spec
    provider_name, model_name = resolve_model(spec)
    provider = get_provider(provider_name)
    provider.model = model_name

    conv = Conversation(
        messages=[Message(role="user", content=prompt)],
        provider=provider_name,
        model=model_name,
    )

    start = time.time()
    event_counts: dict[str, int] = {}
    text_parts: list[str] = []

    async for event in agent_loop(
        provider=provider,
        conversation=conv,
        tools=[],
        system_prompt="You are a terse assistant. Reply in the exact form requested.",
        max_iterations=2,
    ):
        etype = event.type
        event_counts[etype] = event_counts.get(etype, 0) + 1
        if etype == "text" and event.text:
            text_parts.append(event.text)
        elif etype == "error":
            print(f"  [error] {event.error or event.text}")

    elapsed = time.time() - start
    reply = "".join(text_parts).strip()

    print(f"reply:    {reply!r}")
    print(f"elapsed:  {elapsed:.2f}s")
    print(f"events:   {event_counts}")
    print("-" * 72)

    ok = bool(reply) and elapsed < 60
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.exit(asyncio.run(main()))
