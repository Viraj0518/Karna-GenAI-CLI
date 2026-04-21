"""Multi-turn TUI rendering harness — stress-tests prose, tool calls, and
the break rhythm between them.

Invoke::

    PYTHONIOENCODING=utf-8 python tools/tui_screenshot.py [turn...]

Available turns:
    greeting      — short conversational reply
    planning      — numbered plan with nested bullets + bold + inline code
    research      — dense paragraphs + code fence + external links
    brainstorm    — bulleted idea dump with emphasis
    tool          — tool call with result panel (original scenario)
    all           — run every turn in sequence (default)

Used to verify Claude-Code-style rendering across message shapes without
spinning up the full TUI.
"""

from __future__ import annotations

import io
import sys

from rich.console import Console

from karna.tui.output import EventKind, OutputRenderer, StreamEvent


def _fresh_renderer() -> tuple[OutputRenderer, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system="truecolor", width=100)
    return OutputRenderer(console), buf


def _emit_text(r: OutputRenderer, text: str) -> None:
    """Stream text in ~40-char chunks to simulate network deltas."""
    chunk = 40
    for i in range(0, len(text), chunk):
        r.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data=text[i : i + chunk]))


def _finish_turn(r: OutputRenderer, *, prompt_tokens: int, completion_tokens: int, cost: float) -> None:
    r.handle(
        StreamEvent(
            kind=EventKind.USAGE,
            data={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_usd": cost},
        )
    )
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()


# ----------------------------------------------------------------------- #
#  Scenarios
# ----------------------------------------------------------------------- #


def turn_greeting() -> str:
    r, buf = _fresh_renderer()
    r.show_spinner()
    _emit_text(r, "Hey! I'm nellie. What are we working on today?")
    _finish_turn(r, prompt_tokens=148, completion_tokens=14, cost=0.0007)
    return buf.getvalue()


def turn_planning() -> str:
    r, buf = _fresh_renderer()
    r.show_spinner()
    r.handle(
        StreamEvent(
            kind=EventKind.THINKING_DELTA,
            data="User wants to refactor auth. Need to split the plan into design, implementation, tests, rollout.",
        )
    )
    plan = (
        "Here's the plan for the auth refactor:\n\n"
        "**1. Design**\n"
        "- Move token verification out of `middleware.py` into a dedicated "
        "`auth/verifier.py` module.\n"
        "- Decide between **JWT** (stateless) and **session cookies** (stateful) — "
        "I'd recommend JWT for our horizontal-scaling story.\n"
        "- Define a `Principal` dataclass so downstream code stops reaching "
        "into raw dicts.\n\n"
        "**2. Implementation**\n"
        "1. Extract `verify_token()` into `auth/verifier.py` (pure function, no Flask deps).\n"
        "2. Replace `request.user` with `g.principal` populated by a `before_request` hook.\n"
        "3. Deprecate the old helpers with a one-cycle warning period.\n\n"
        "**3. Tests**\n"
        "- Add unit tests for `verify_token()` against the 8 existing JWT fixtures.\n"
        "- Migrate the integration tests that currently mock `request.user`.\n\n"
        "**4. Rollout**\n"
        "- Feature-flag the new middleware behind `AUTH_V2_ENABLED`.\n"
        "- Burn in on staging for 48h before flipping production.\n\n"
        "Want me to start on step 1?"
    )
    _emit_text(r, plan)
    _finish_turn(r, prompt_tokens=2340, completion_tokens=226, cost=0.014)
    return buf.getvalue()


def turn_research() -> str:
    r, buf = _fresh_renderer()
    r.show_spinner()
    research = (
        "I dug through the codebase and the Python 3.12 release notes. Three "
        "findings matter for this migration:\n\n"
        "First, `typing.TypedDict` gained `Required`/`NotRequired` in 3.11, which "
        "means we can drop the `typing_extensions` shim in `karna/models.py`. "
        "That removes one install-time dependency across all three VMs.\n\n"
        "Second, `asyncio.TaskGroup` is the new recommended way to structure "
        "concurrent work — it's strictly better than `asyncio.gather()` for our "
        "use case because it cancels siblings on first failure. Sample:\n\n"
        "```python\n"
        "async with asyncio.TaskGroup() as tg:\n"
        "    tg.create_task(fetch_provider_a())\n"
        "    tg.create_task(fetch_provider_b())\n"
        "    tg.create_task(fetch_provider_c())\n"
        "```\n\n"
        "Third — and this is the one I'm least sure about — the new "
        "`PEP 695` generic syntax (`class Foo[T]:`) would simplify our "
        "provider abstractions but breaks IDE support in about 30% of "
        "installs. Worth deferring until 3.13 lands in LTS distros.\n\n"
        "Source: [Python 3.12 What's New](https://docs.python.org/3.12/whatsnew/3.12.html)"
    )
    _emit_text(r, research)
    _finish_turn(r, prompt_tokens=3890, completion_tokens=312, cost=0.019)
    return buf.getvalue()


def turn_brainstorm() -> str:
    r, buf = _fresh_renderer()
    r.show_spinner()
    brainstorm = (
        "Brainstorm for reducing first-paint latency on the dashboard — ranked "
        "by impact vs. effort:\n\n"
        "- **Server-side render the first fold** (high impact, 2-day effort). "
        "The 300ms client-side hydration is killing us on mobile.\n"
        "- **Ship a smaller JS bundle for the shell** — we're pulling in the "
        "entire chart library even on the login page. Code-split it.\n"
        "- **Replace webfonts with `font-display: swap`** so text isn't blocked "
        "on the 200KB Inter download.\n"
        "- **Move the API calls for critical data to streaming SSR** rather "
        "than the current fetch-in-useEffect pattern.\n"
        "- Cache the shell HTML at the edge (Cloudflare KV) — sub-50ms TTFB "
        "for cold users.\n"
        "- Debounced prefetching of the routes the user is most likely to "
        "hit next (e.g. `/dashboard` from `/`).\n\n"
        "None of these are exclusive. If I had to pick two for this sprint: "
        "**SSR the first fold** and **code-split the shell**. They attack "
        "the p50 and p95 from different directions."
    )
    _emit_text(r, brainstorm)
    _finish_turn(r, prompt_tokens=1820, completion_tokens=198, cost=0.011)
    return buf.getvalue()


def turn_tool() -> str:
    r, buf = _fresh_renderer()
    r.show_spinner()
    r.handle(StreamEvent(kind=EventKind.THINKING_DELTA, data="Need to look at the file first."))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "read", "id": "t1"}))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"file_path": "karna/tui/output.py"}'))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    r.handle(
        StreamEvent(
            kind=EventKind.TOOL_RESULT,
            data={"content": "\n".join(f"line {i}" for i in range(62)), "is_error": False},
        )
    )
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "bash", "id": "t2"}))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"command": "pytest tests/test_tui.py -q"}'))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    r.handle(StreamEvent(kind=EventKind.TOOL_RESULT, data={"content": "28 passed in 0.42s", "is_error": False}))
    _emit_text(r, "All 28 tests pass. The Claude-Code-style glyphs are rendering correctly.")
    _finish_turn(r, prompt_tokens=2145, completion_tokens=186, cost=0.0132)
    return buf.getvalue()


# ----------------------------------------------------------------------- #
#  CLI
# ----------------------------------------------------------------------- #


TURNS = {
    "greeting": ("User: hey!", turn_greeting),
    "planning": ("User: plan a refactor of our auth middleware.", turn_planning),
    "research": ("User: what's new in Python 3.12 that affects us?", turn_research),
    "brainstorm": ("User: brainstorm ways to reduce first-paint latency.", turn_brainstorm),
    "tool": ("User: run the TUI tests and summarise.", turn_tool),
}


def main(argv: list[str]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:
        pass

    if argv and argv[0] != "all":
        turns = [argv[0]]
    else:
        turns = list(TURNS.keys())

    for name in turns:
        if name not in TURNS:
            print(f"unknown turn: {name!r} — available: {', '.join(TURNS)}", file=sys.stderr)
            continue
        label, fn = TURNS[name]
        sys.stdout.write(f"\n\x1b[1;38;5;245m{label}\x1b[0m\n")
        sys.stdout.write(fn())


if __name__ == "__main__":
    main(sys.argv[1:])
