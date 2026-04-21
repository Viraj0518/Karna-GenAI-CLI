"""Capture 'after' SVG snapshots of the redesigned Nellie TUI.

Mirrors ``capture_before.py`` exactly — same 10 scenes, same rendering
recipe (Rich ``Console.export_svg()`` at 100 cols, ``KARNA_THEME``,
``force_terminal=True``). The only difference: consumes the refactored
``karna.tui.*`` modules (design_tokens, icons, banner, output, slash)
so the SVGs reflect the new visual language:

* Semantic design tokens from ``karna.tui.design_tokens``
* Nerd Font glyphs (forced on for deterministic snapshots) from
  ``karna.tui.icons``
* The redesigned ``print_banner`` with workspace detection
* ``OutputRenderer`` with distinct tool-call lifecycle states, a role
  header on assistant output, and recovery hints on errors
* ``_cmd_help``'s grouped panel

Run:
    python research/ui-audit/capture_after.py

Output:
    research/ui-audit/after/NN_name.svg
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Callable

# Force Nerd Font + truecolor for the screenshots so SVGs are consistent
# across capture machines regardless of the host terminal.
os.environ["KARNA_NERD_FONT"] = "1"

from rich.console import Console

from karna.config import KarnaConfig
from karna.tui.banner import print_banner
from karna.tui.design_tokens import SEMANTIC
from karna.tui.icons import IconSet
from karna.tui.icons import icons as _default_icons
from karna.tui.output import EventKind, OutputRenderer, StreamEvent
from karna.tui.slash import _cmd_help
from karna.tui.themes import KARNA_THEME

# --------------------------------------------------------------------------- #
#  Setup
# --------------------------------------------------------------------------- #

OUT = Path(__file__).parent / "after"
OUT.mkdir(parents=True, exist_ok=True)

TOOLS_LOADED = [
    "bash",
    "read",
    "write",
    "edit",
    "grep",
    "glob",
    "git",
    "web_fetch",
    "web_search",
    "clipboard",
    "image",
    "mcp",
    "task",
    "monitor",
]

# IconSet's nerd-font detection requires a TTY; we're capturing to a
# StringIO buffer, so flip the flag explicitly for deterministic output.
try:
    _default_icons._use_nerd = True  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    pass


def _make_console() -> Console:
    """Fresh Console with recording enabled for SVG export.

    Routes output to a utf-8 StringIO so Nerd Font glyphs round-trip
    cleanly on Windows terminals whose default codepage is cp1252.
    """
    buf = io.StringIO()
    return Console(
        record=True,
        width=100,
        theme=KARNA_THEME,
        force_terminal=True,
        color_system="truecolor",
        file=buf,
    )


def snapshot(name: str, render: Callable[[Console], None]) -> None:
    """Render a scene to a Console and dump the SVG."""
    console = _make_console()
    render(console)
    svg = console.export_svg(title=f"Nellie — {name}")
    out_path = OUT / f"{name}.svg"
    out_path.write_text(svg, encoding="utf-8")
    print(f"[ok] wrote {out_path.name}")


# --------------------------------------------------------------------------- #
#  Scenes — mirror of capture_before.py
# --------------------------------------------------------------------------- #


def scene_banner(c: Console) -> None:
    """Redesigned banner via the real print_banner API."""
    cfg = KarnaConfig(
        active_provider="openrouter",
        active_model="openai/gpt-oss-120b",
    )
    print_banner(c, cfg, tool_names=TOOLS_LOADED)


def scene_empty_prompt(c: Console) -> None:
    """Simulate the idle input prompt (prompt_toolkit isn't renderable here).

    Mirrors karna.tui.input: prompt string is ``<model-short>> `` in
    bold accent.cyan. The after version adds a brand-blue chevron
    affordance and a muted cursor stub.
    """
    icons = IconSet()
    icons._use_nerd = True  # type: ignore[attr-defined]
    cyan = SEMANTIC["accent.cyan"]
    brand = SEMANTIC["accent.brand"]
    tert = SEMANTIC["text.tertiary"]
    c.print(
        f"[{brand}]{icons.chevron_right}[/] [bold {cyan}]gpt-oss-120b[/] [{tert}]{icons.arrow_right}[/] ",
        end="",
    )
    c.print(f"[{tert}]▌[/]")


def scene_user_msg_echo(c: Console) -> None:
    """Scrollback view right after the user hits enter."""
    icons = IconSet()
    icons._use_nerd = True  # type: ignore[attr-defined]
    cyan = SEMANTIC["accent.cyan"]
    brand = SEMANTIC["accent.brand"]
    tert = SEMANTIC["text.tertiary"]
    primary = SEMANTIC["text.primary"]
    c.print(
        f"[{brand}]{icons.chevron_right}[/] "
        f"[bold {cyan}]gpt-oss-120b[/] "
        f"[{tert}]{icons.arrow_right}[/] "
        f"[{primary}]write a python script that prints the first 12 fibonacci numbers[/]"
    )
    c.print()


def scene_assistant_streaming(c: Console) -> None:
    """Mid-stream assistant response via TEXT_DELTA events (real API)."""
    renderer = OutputRenderer(c)
    chunks = [
        "Sure — I'll create a small Python script that prints the first 12 ",
        "Fibonacci numbers using an iterative approach (O(n) time, O(1) ",
        "space). Here's the plan:\n\n",
        "1. Start with `a, b = 0, 1`\n",
        "2. Loop 12 times, printing `a` each iteration\n",
        "3. Update `a, b = b, a + b`\n\n",
        "Let me write the file now...",
    ]
    for chunk in chunks:
        renderer.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data=chunk))
    renderer.finish()


def scene_tool_call(c: Console) -> None:
    """write() tool call with path + content streaming in (real API)."""
    renderer = OutputRenderer(c)
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_CALL_START,
            data={"name": "write", "id": "call_01"},
        )
    )
    args_chunks = [
        '{"path": "fib.py", ',
        '"content": "def fib(n):\\n    a, b = 0, 1\\n',
        "    for _ in range(n):\\n        print(a)\\n",
        '        a, b = b, a + b\\n\\nfib(12)\\n"}',
    ]
    for chunk in args_chunks:
        renderer.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data=chunk))
    renderer.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))


def scene_tool_result(c: Console) -> None:
    """Result panel from the write tool (real API)."""
    renderer = OutputRenderer(c)
    # Need a _tool present so the result renders with the right name.
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_CALL_START,
            data={"name": "write", "id": "call_01"},
        )
    )
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_CALL_ARGS_DELTA,
            data='{"path": "fib.py"}',
        )
    )
    renderer.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_RESULT,
            data={"content": "wrote 12 lines to fib.py", "is_error": False},
        )
    )


def scene_thinking(c: Console) -> None:
    """The spinner/thinking state.

    ``OutputRenderer.show_spinner`` uses ``rich.live.Live`` which is
    transient and can't be recorded. The new design flushes reasoning
    via ``THINKING_DELTA``, so we exercise that path to capture the
    redesigned one-liner.
    """
    renderer = OutputRenderer(c)
    renderer.handle(
        StreamEvent(
            kind=EventKind.THINKING_DELTA,
            data="planning the file write — iterative fib, O(1) space",
        )
    )
    renderer.finish()


def scene_error(c: Console) -> None:
    """401 Unauthorized error via the real ERROR event path."""
    renderer = OutputRenderer(c)
    msg = (
        "Agent error: HTTPStatusError: Client error '401 Unauthorized' for url "
        "'https://openrouter.ai/api/v1/chat/completions'. For more information "
        "check: https://httpstatuses.com/401"
    )
    renderer.handle(StreamEvent(kind=EventKind.ERROR, data=msg))


def scene_slash_help(c: Console) -> None:
    """/help output via the real _cmd_help handler."""
    _cmd_help(console=c)


def scene_multi_tool(c: Console) -> None:
    """Two tool calls in sequence: read then edit (real API)."""
    renderer = OutputRenderer(c)

    # read
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_CALL_START,
            data={"name": "read", "id": "call_02"},
        )
    )
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_CALL_ARGS_DELTA,
            data='{"path": "fib.py"}',
        )
    )
    renderer.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_RESULT,
            data={
                "content": (
                    "def fib(n):\n"
                    "    a, b = 0, 1\n"
                    "    for _ in range(n):\n"
                    "        print(a)\n"
                    "        a, b = b, a + b\n"
                    "\n"
                    "fib(12)\n"
                ),
                "is_error": False,
            },
        )
    )

    # edit
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_CALL_START,
            data={"name": "edit", "id": "call_03"},
        )
    )
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_CALL_ARGS_DELTA,
            data=(
                '{"path": "fib.py", '
                '"old_string": "fib(12)", '
                '"new_string": "if __name__ == \\"__main__\\":\\n    fib(12)"}'
            ),
        )
    )
    renderer.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_RESULT,
            data={"content": "edited fib.py (1 replacement)", "is_error": False},
        )
    )


# --------------------------------------------------------------------------- #
#  Driver
# --------------------------------------------------------------------------- #

SCENES: list[tuple[str, Callable[[Console], None]]] = [
    ("01_banner", scene_banner),
    ("02_empty_prompt", scene_empty_prompt),
    ("03_user_msg_echo", scene_user_msg_echo),
    ("04_assistant_streaming", scene_assistant_streaming),
    ("05_tool_call", scene_tool_call),
    ("06_tool_result", scene_tool_result),
    ("07_thinking", scene_thinking),
    ("08_error", scene_error),
    ("09_slash_help", scene_slash_help),
    ("10_multi_tool", scene_multi_tool),
]


def main() -> None:
    for name, render in SCENES:
        try:
            snapshot(name, render)
        except Exception as exc:  # pragma: no cover - diagnostic path
            print(f"[err] {name}: {type(exc).__name__}: {exc}")
    print(f"\nAll SVGs written to: {OUT}")


if __name__ == "__main__":
    main()
