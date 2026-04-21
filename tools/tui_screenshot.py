"""Capture the new TUI's rendering for a representative turn.

Feeds a synthetic event stream through ``OutputRenderer`` against a
force_terminal Rich console and writes the output to stdout. Run::

    python tools/tui_screenshot.py

…or with ``PYTHONIOENCODING=utf-8`` on Windows. Used to spot-check
Claude-Code-style glyphs without starting the full TUI.
"""

from __future__ import annotations

import io
import sys

from rich.console import Console

from karna.tui.output import EventKind, OutputRenderer, StreamEvent


def main() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system="truecolor", width=100)
    renderer = OutputRenderer(console)

    # Turn 1 — thinking → tool call (read) → tool call (bash) → reply.
    renderer.show_spinner()
    renderer.handle(StreamEvent(kind=EventKind.THINKING_DELTA, data="Let me look at the file first."))
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_CALL_START,
            data={"name": "read", "id": "t1"},
        )
    )
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_CALL_ARGS_DELTA,
            data='{"file_path": "karna/tui/output.py"}',
        )
    )
    renderer.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_RESULT,
            data={"content": "\n".join([f"line {i}" for i in range(62)]), "is_error": False},
        )
    )
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_CALL_START,
            data={"name": "bash", "id": "t2"},
        )
    )
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_CALL_ARGS_DELTA,
            data='{"command": "pytest tests/test_tui.py -q"}',
        )
    )
    renderer.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_RESULT,
            data={"content": "28 passed in 0.42s", "is_error": False},
        )
    )
    renderer.handle(
        StreamEvent(
            kind=EventKind.TEXT_DELTA,
            data="All 28 tests pass. The Claude-Code-style glyphs are rendering correctly.",
        )
    )
    renderer.handle(StreamEvent(kind=EventKind.USAGE, data={"prompt_tokens": 2145, "completion_tokens": 186, "total_usd": 0.0132}))
    renderer.handle(StreamEvent(kind=EventKind.DONE))
    renderer.finish()

    # Print to real stdout (reconfigured to utf-8 on Windows).
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:
        pass
    sys.stdout.write(buf.getvalue())


if __name__ == "__main__":
    main()
