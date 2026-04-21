"""100-turn stress drive against a real provider.

Builds a persistent Conversation, fires 100 varied user messages through
``agent_loop`` end-to-end, streams every event, and prints a compact
per-turn line plus an aggregate summary. Failures don't abort — we want
to know which turns broke and why.

Model: ``openrouter:openai/gpt-oss-120b:free`` by default (free, fast,
no cost per token for a stress run). Override with argv.
"""

from __future__ import annotations

import asyncio
import sys
import time

from karna.agents.loop import agent_loop
from karna.models import Conversation, Message
from karna.providers import get_provider, resolve_model

PROMPTS = [
    # Conversational (1-10)
    "Hi — who are you in one sentence?",
    "What's your favourite data structure and why?",
    "Roast procrastination in one line.",
    "Give me a mantra for debugging.",
    "Compose a haiku about merge conflicts.",
    "What's 2 + 2?",
    "Pick a colour.",
    "If you were a shell command, which one?",
    "Two-word review of Python.",
    "Say hi in Klingon.",
    # Coding (11-30)
    "Write a Python one-liner to flatten a list of lists.",
    "Explain async/await in 2 sentences.",
    "What's wrong with `except Exception: pass`?",
    "Show me a tiny FastAPI endpoint.",
    "One-line regex for an email address (good-enough, not perfect).",
    "Reverse a linked list — just the body, no boilerplate.",
    "Difference between deepcopy and copy in 20 words.",
    "Write a memoize decorator.",
    "What does `yield from` do?",
    "Singleton pattern in Python — ≤5 lines.",
    "Explain GIL in one sentence.",
    "When should I use dataclasses vs TypedDict?",
    "Show me a generator expression that sums squares 1..10.",
    "Difference between is and ==.",
    "One-liner to check if a string is palindrome.",
    "Write an LRU cache decorator without functools.",
    "Explain duck typing in 15 words.",
    "What's `__slots__` for?",
    "Convert `for i in range(len(x)): ... x[i]` to enumerate.",
    "Name three cases where a set is better than a list.",
    # Planning / structure (31-50)
    "Outline steps to migrate a Flask app to FastAPI.",
    "How would you load-test a REST API?",
    "Design a URL shortener's data model — 5 bullet points.",
    "What metrics matter for an LLM API?",
    "Structure a bug report.",
    "Top 3 ways to reduce Python import time.",
    "Checklist before merging a PR.",
    "Outline a retry policy for flaky HTTP calls.",
    "What's in a good on-call runbook?",
    "Plan a 2-day spike on Postgres partitioning.",
    "Pros and cons of monorepo.",
    "Which cache invalidation strategy for a product catalog?",
    "How do you choose a queue: SQS vs Kafka?",
    "Outline a blameless post-mortem template.",
    "Three metrics for developer happiness.",
    "Name three anti-patterns in microservices.",
    "Plan a database migration with zero downtime.",
    "What goes in an SLO doc?",
    "Three tests every new feature needs.",
    "Outline an onboarding doc for a junior engineer.",
    # Trivia / factoid (51-70)
    "What year was Python released?",
    "Who wrote 'The Pragmatic Programmer'?",
    "What does HTTP 418 mean?",
    "Name an architect-famous German term meaning 'world view'.",
    "Longest word you know with no repeating letters.",
    "What's the RFC number for HTTP/1.1?",
    "Who invented the web?",
    "What's the speed of light, rounded?",
    "Who designed the ampersand?",
    "What's the capital of Estonia?",
    "Name three Unix commands that start with 'g'.",
    "When did Git 1.0 ship?",
    "Who wrote 'Design Patterns'?",
    "What's the answer to life, the universe, and everything?",
    "Which country invented the decimal system?",
    "When was TCP/IP standardized?",
    "What's a palindromic prime example under 100?",
    "Who was Alan Turing's contemporary at Bletchley Park?",
    "What's the oldest still-spoken language?",
    "What's the Higgs boson, in one sentence?",
    # Creative / imaginative (71-90)
    "Invent a band name with the word 'stream' in it.",
    "Pitch me a startup idea in one line.",
    "Describe winter in Seattle in 10 words.",
    "Name a fictional programming language — just the name.",
    "Write a fortune-cookie line for engineers.",
    "Describe nostalgia in a single sentence.",
    "Name three animals that would be bad at code review.",
    "What would a sentient compiler's Twitter bio read?",
    "Invent a cocktail named after async/await.",
    "First line of a sci-fi novel about version control.",
    "Give me a conspiracy theory about CamelCase.",
    "Write a 5-word horror story.",
    "If Python had a personality, describe it.",
    "Invent a productivity hack that's obviously fake.",
    "Describe the smell of a datacenter.",
    "Pitch a movie called 'The Refactoring'.",
    "Name three forbidden CSS properties.",
    "What would dogs name themselves?",
    "Write a tweet from a bored GPU.",
    "Describe the Metaverse in the voice of a grumpy sysadmin.",
    # Self-test (91-100) — validates memory of the conversation
    "Have we talked before this session? Yes or no.",
    "What was my very first message?",
    "How many prompts have I sent you so far (approximately)?",
    "Was my second prompt about a data structure?",
    "Recall: what did I ask about Klingon?",
    "In one word, describe the pace of this conversation.",
    "Which of these: Python, Klingon, merge conflicts — did I mention first?",
    "What's the last thing you told me?",
    "Give me a one-line summary of this whole chat.",
    "Sign off with a two-word phrase.",
]

assert len(PROMPTS) == 100, f"need 100 prompts, got {len(PROMPTS)}"


def _fmt_elapsed(s: float) -> str:
    if s < 1.0:
        return f"{int(s * 1000)}ms"
    return f"{s:.1f}s"


async def main() -> int:
    spec = sys.argv[1] if len(sys.argv) > 1 else "openrouter:openai/gpt-oss-120b:free"
    provider_name, model_name = resolve_model(spec)
    provider = get_provider(provider_name)
    provider.model = model_name

    print(f"provider: {provider_name}")
    print(f"model:    {model_name}")
    print(f"turns:    {len(PROMPTS)}")
    print("=" * 78)

    conv = Conversation(provider=provider_name, model=model_name)
    system = (
        "You are Nellie, a terse, helpful agent. Reply in at most two sentences "
        "unless the user explicitly asks for more. No preamble."
    )

    stats = {
        "ok": 0,
        "empty": 0,
        "error": 0,
        "total_time": 0.0,
        "total_in": 0,
        "total_out": 0,
    }
    fail_log: list[tuple[int, str, str]] = []

    for i, prompt in enumerate(PROMPTS, 1):
        conv.messages.append(Message(role="user", content=prompt))
        t0 = time.time()
        text_parts: list[str] = []
        usage = {"in": 0, "out": 0}
        err: str | None = None
        try:
            async for ev in agent_loop(
                provider=provider,
                conversation=conv,
                tools=[],
                system_prompt=system,
                max_iterations=2,
            ):
                if ev.type == "text" and ev.text:
                    text_parts.append(ev.text)
                elif ev.type == "error":
                    err = (ev.error or ev.text or "unknown")[:200]
                elif ev.type == "usage" and ev.usage:
                    usage["in"] = getattr(ev.usage, "input_tokens", 0) or 0
                    usage["out"] = getattr(ev.usage, "output_tokens", 0) or 0
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"[:200]
        elapsed = time.time() - t0
        reply = "".join(text_parts).strip()

        if err:
            stats["error"] += 1
            fail_log.append((i, prompt, f"ERROR: {err}"))
            flag = "\u2717"  # ✗
        elif not reply:
            stats["empty"] += 1
            fail_log.append((i, prompt, "EMPTY_REPLY"))
            flag = "\u26a0"  # ⚠
        else:
            stats["ok"] += 1
            flag = "\u2713"  # ✓

        stats["total_time"] += elapsed
        stats["total_in"] += usage["in"]
        stats["total_out"] += usage["out"]

        one_line_reply = reply.replace("\n", " ")[:72]
        print(f"[{i:3d}] {flag} {_fmt_elapsed(elapsed):>7}  {usage['in']:>5}↓ {usage['out']:>4}↑  {one_line_reply}")
        if err:
            print(f"     └─ {err}")

        # Append the assistant reply so the next turn has the context
        # (this is what makes it a real conversation rather than 100
        # isolated one-shots).
        conv.messages.append(Message(role="assistant", content=reply or ""))

    print("=" * 78)
    print(
        f"summary: {stats['ok']} ok · {stats['empty']} empty · {stats['error']} errors "
        f"· total {_fmt_elapsed(stats['total_time'])} "
        f"· {stats['total_in']:,} in / {stats['total_out']:,} out"
    )
    if fail_log:
        print(f"\n{len(fail_log)} failure(s):")
        for i, p, e in fail_log[:20]:
            print(f"  [{i}] {p[:60]!r} → {e}")
    return 0 if stats["error"] == 0 and stats["empty"] <= 2 else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.exit(asyncio.run(main()))
