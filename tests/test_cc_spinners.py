"""Tests for the CC-ported spinner + tool-use loader module.

Covers the `karna.tui.cc_components.spinners` library surface:
  * frame tables (BRAILLE_FRAMES, SPINNER_FRAMES)
  * TOOL_MESSAGES dict + `pick_tool_message`
  * `render_thinking_line` ANSI shape
  * `render_tool_loader` Rich renderable
  * `render_bash_progress`
  * `render_agent_progress_line`
  * `render_coordinator_status`
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from karna.tui.cc_components import spinners
from karna.tui.cc_components.spinners import (
    ALL_SPINNER_VERBS,
    BRAILLE_FRAMES,
    SPINNER_FRAMES,
    THINKING_GLYPH,
    TOOL_MESSAGES,
    pick_tool_message,
    render_agent_progress_line,
    render_bash_progress,
    render_coordinator_status,
    render_thinking_line,
    render_tool_loader,
)


# --------------------------------------------------------------------------- #
#  Frame tables
# --------------------------------------------------------------------------- #


def test_braille_frames_shape() -> None:
    """Braille spinner has 10 frames and each is a single char."""
    assert len(BRAILLE_FRAMES) == 10
    assert all(isinstance(f, str) and len(f) == 1 for f in BRAILLE_FRAMES)


def test_spinner_frames_are_mirrored_and_nonempty() -> None:
    """CC's SPINNER_FRAMES = [...chars, ...reversed(chars)] — even length."""
    assert len(SPINNER_FRAMES) > 0
    assert len(SPINNER_FRAMES) % 2 == 0
    half = len(SPINNER_FRAMES) // 2
    assert SPINNER_FRAMES[:half] == list(reversed(SPINNER_FRAMES[half:]))


# --------------------------------------------------------------------------- #
#  Tool messages
# --------------------------------------------------------------------------- #


def test_tool_messages_cover_core_nellie_tools() -> None:
    """Every tool Nellie's output.py advertises should have a verb bucket."""
    required = {
        "bash", "read", "write", "edit", "grep", "glob",
        "git", "web_search", "web_fetch", "task", "thinking",
    }
    assert required.issubset(TOOL_MESSAGES.keys())
    for key, bucket in TOOL_MESSAGES.items():
        assert isinstance(bucket, list) and bucket, f"{key} has empty bucket"
        # Every curated verb must come from CC's master list (no invented words).
        assert all(v in ALL_SPINNER_VERBS for v in bucket), (
            f"{key} contains words not in CC's SPINNER_VERBS"
        )


def test_pick_tool_message_is_deterministic_with_seed() -> None:
    """Same seed → same verb; unknown tool → falls back to ALL_SPINNER_VERBS."""
    a = pick_tool_message("bash", seed=3)
    b = pick_tool_message("bash", seed=3)
    assert a == b
    assert a in TOOL_MESSAGES["bash"]

    # Unknown tool falls through to the full list.
    unknown = pick_tool_message("definitely_not_a_tool", seed=0)
    assert unknown in ALL_SPINNER_VERBS


# --------------------------------------------------------------------------- #
#  Thinking line
# --------------------------------------------------------------------------- #


def test_render_thinking_line_shape_and_glyph() -> None:
    """`✦ Thinking · 4s · ↑ 2.1k tok · esc` — ANSI-wrapped but substring-checkable."""
    out = render_thinking_line(elapsed_s=4.0, token_count=2100)
    # Strip ANSI for content assertions
    import re
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)

    assert THINKING_GLYPH in plain
    assert "Thinking" in plain
    assert "4s" in plain
    assert "2.1k tok" in plain
    assert "↑" in plain
    assert "esc" in plain
    # CC's middot separator
    assert "·" in plain

    # No token_count, no esc hint
    minimal = render_thinking_line(elapsed_s=2.0, show_esc_hint=False)
    minimal_plain = re.sub(r"\x1b\[[0-9;]*m", "", minimal)
    assert "esc" not in minimal_plain
    assert "tok" not in minimal_plain
    assert "2s" in minimal_plain


# --------------------------------------------------------------------------- #
#  Tool loader
# --------------------------------------------------------------------------- #


def test_render_tool_loader_emits_header_and_detail() -> None:
    """Loader renders a `● Tool(ctx)` header + `⎿ verb…` detail."""
    loader = render_tool_loader("Read", context="src/main.py", elapsed_s=1.5)
    console = Console(record=True, width=80, force_terminal=True)
    console.print(loader)
    out = console.export_text()
    assert "●" in out
    assert "Read" in out
    assert "src/main.py" in out
    assert "⎿" in out
    assert "…" in out


def test_render_tool_loader_done_vs_error_styling() -> None:
    """Done and error states should render without crashing and include the dot."""
    done = render_tool_loader("Bash", context="ls", elapsed_s=0.5, is_done=True)
    err = render_tool_loader("Bash", context="ls", elapsed_s=0.5, is_error=True)
    console = Console(record=True, width=80, force_terminal=True)
    console.print(done)
    console.print(err)
    out = console.export_text()
    assert out.count("●") >= 2


# --------------------------------------------------------------------------- #
#  Bash progress
# --------------------------------------------------------------------------- #


def test_render_bash_progress_shape() -> None:
    """`$ cmd` header + `⎿ <frame> running · Ns · N lines so far`."""
    r = render_bash_progress("pytest -k foo", elapsed_s=12.0, output_lines=42)
    console = Console(record=True, width=80, force_terminal=True)
    console.print(r)
    out = console.export_text()
    assert "pytest -k foo" in out
    assert "running" in out
    assert "12s" in out
    assert "42 lines" in out


# --------------------------------------------------------------------------- #
#  Agent progress line
# --------------------------------------------------------------------------- #


def test_render_agent_progress_line_tree_chars() -> None:
    """Non-last row uses ├─; last row uses └─."""
    mid = render_agent_progress_line("agent-1", status="Running", current_tool="Read",
                                     is_last=False, tool_use_count=3, tokens=2100)
    last = render_agent_progress_line("agent-2", status="Done",
                                      is_last=True, tool_use_count=1)
    assert isinstance(mid, Text)
    assert isinstance(last, Text)
    assert "├─" in mid.plain
    assert "└─" in last.plain
    assert "agent-1" in mid.plain
    assert "3 tool uses" in mid.plain
    assert "2.1k tokens" in mid.plain
    assert "(Read)" in mid.plain
    assert "1 tool use" in last.plain  # singular


# --------------------------------------------------------------------------- #
#  Coordinator status
# --------------------------------------------------------------------------- #


def test_render_coordinator_status_empty_and_populated() -> None:
    """Empty list → `(no agents running)`; populated → one row per agent."""
    empty = render_coordinator_status([])
    console = Console(record=True, width=80, force_terminal=True)
    console.print(empty)
    out = console.export_text()
    assert "Agents" in out
    assert "no agents running" in out

    populated = render_coordinator_status([
        {"id": "main", "status": "running", "tool_use_count": 0},
        {"id": "writer", "status": "writing", "current_tool": "Write",
         "tool_use_count": 2, "tokens": 1500},
    ])
    console2 = Console(record=True, width=80, force_terminal=True)
    console2.print(populated)
    out2 = console2.export_text()
    assert "main" in out2
    assert "writer" in out2
    assert "writing" in out2
    assert "2 tool uses" in out2
    assert "1.5k tok" in out2


# --------------------------------------------------------------------------- #
#  Module does not pull in the REPL (constraint from the task spec)
# --------------------------------------------------------------------------- #


def test_spinners_module_does_not_import_repl() -> None:
    """Hard constraint: spinners.py must stay self-contained."""
    src = open(spinners.__file__, encoding="utf-8").read()
    assert "from karna.tui.repl" not in src
    assert "from karna.tui.hermes_repl" not in src
    assert "import karna.tui.repl" not in src
    assert "import karna.tui.hermes_repl" not in src
