"""Capture 'before' SVG snapshots of the current Nellie TUI rendering.

Exercises the real karna.tui APIs (no mocks) and uses Rich's
``Console.export_svg()`` to snapshot each scene to a standalone SVG.
Works without a real TTY, so it runs cleanly under Git Bash / CI.

Run:
    python research/ui-audit/capture_before.py

Output:
    research/ui-audit/before/NN_name.svg
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.table import Table

from karna.config import KarnaConfig
from karna.tui.banner import print_banner
from karna.tui.output import EventKind, OutputRenderer, StreamEvent
from karna.tui.slash import COMMANDS
from karna.tui.themes import KARNA_THEME

OUT = Path(__file__).parent / "before"
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


def _make_console() -> Console:
    """Fresh Console with recording enabled for SVG export.

    We pipe Rich's output to an in-memory UTF-8 buffer (not real stdout)
    so Windows' cp1252 console encoding doesn't choke on spinner /
    block-cursor glyphs. The SVG is still exported from the recorded
    segments — the sink is only to avoid a side-effect UnicodeEncodeError.
    """
    sink = io.StringIO()
    return Console(
        record=True,
        width=100,
        theme=KARNA_THEME,
        force_terminal=True,
        color_system="truecolor",
        file=sink,
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
# Scenes
# --------------------------------------------------------------------------- #


def scene_banner(c: Console) -> None:
    cfg = KarnaConfig(
        active_provider="openrouter",
        active_model="openai/gpt-oss-120b",
    )
    print_banner(c, cfg, tool_names=TOOLS_LOADED)


def scene_empty_prompt(c: Console) -> None:
    """Simulate the idle input prompt (prompt_toolkit isn't renderable here).

    Mirrors the styling from karna.tui.repl._make_session where the prompt
    is '<model-short>> ' in bold #87CEEB.
    """
    # Banner ends with a blank line; show the prompt with a blinking-cursor stub.
    c.print("[bold #87CEEB]gpt-oss-120b> [/]", end="")
    c.print("[dim]▌[/]")  # cursor stub on same visual line


def scene_user_msg_echo(c: Console) -> None:
    """What the scrollback looks like after the user hits enter.

    prompt_toolkit echoes the typed line above the rendered output. We
    reproduce that visual here so the SVG captures the "user line" state.
    """
    c.print("[bold #87CEEB]gpt-oss-120b> [/]write a python script that prints the first 12 fibonacci numbers")
    c.print()


def scene_assistant_streaming(c: Console) -> None:
    """Mid-stream assistant response via TEXT_DELTA events."""
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
    renderer.finish()  # flushes the markdown-rendered assistant block


def scene_tool_call(c: Console) -> None:
    """write() tool call with path + content streaming in."""
    renderer = OutputRenderer(c)
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_CALL_START,
            data={"name": "write", "id": "call_01"},
        )
    )
    # Simulate args JSON streaming in deltas
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
    """Result panel from the write tool."""
    renderer = OutputRenderer(c)
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_RESULT,
            data={"content": "wrote 12 lines to fib.py", "is_error": False},
        )
    )


def scene_thinking(c: Console) -> None:
    """The spinner 'thinking...' state.

    Note: OutputRenderer.show_spinner() uses rich.live.Live which only
    renders to an attached TTY. To capture a representative SVG we
    render a static frame of the same spinner symbol + text with the
    identical styling (bold BRAND_BLUE).
    """
    from karna.tui.themes import BRAND_BLUE

    # First dots-spinner frame char is '⠋'; match the Live output shape.
    c.print(f"[bold {BRAND_BLUE}]⠋[/] [bold {BRAND_BLUE}]thinking...[/]")


def scene_error(c: Console) -> None:
    """401 Unauthorized error panel (via the normal ERROR event path)."""
    renderer = OutputRenderer(c)
    msg = (
        "Agent error: HTTPStatusError: Client error '401 Unauthorized' for url "
        "'https://openrouter.ai/api/v1/chat/completions'\n"
        "For more information check: https://httpstatuses.com/401"
    )
    renderer.handle(StreamEvent(kind=EventKind.ERROR, data=msg))


def scene_slash_help(c: Console) -> None:
    """/help output — rebuilds the help table from slash.COMMANDS."""
    table = Table(
        show_header=True,
        header_style="bold #87CEEB",
        border_style="#3C73BD",
        expand=False,
    )
    table.add_column("Command", style="white")
    table.add_column("Description", style="bright_black")
    for cmd in COMMANDS.values():
        if cmd.name == "quit":
            continue
        table.add_row(cmd.usage, cmd.help_text)
    c.print(table)


def scene_multi_tool(c: Console) -> None:
    """Two tool calls in sequence: read then edit."""
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
# Driver
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
