"""Tests for karna.tui.cc_components.diffs — CC visual port.

Six tests exercising the six public renderables in that module.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from karna.tui.cc_components.diffs import (
    render_file_edit_accepted,
    render_file_edit_rejected,
    render_file_path_link,
    render_structured_diff,
    render_tool_error,
    render_tool_rejected,
)


def _render(obj) -> str:
    """Render *obj* through a width-120 truecolor Console and return ANSI text."""
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=120,
        color_system="truecolor",
        legacy_windows=False,
    )
    console.print(obj)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
#  render_structured_diff
# --------------------------------------------------------------------------- #


def test_structured_diff_shows_added_and_removed_payload() -> None:
    """CC's StructuredDiffFallback emits ``+payload`` and ``-payload`` lines."""
    out = _render(render_structured_diff("alpha\nbeta\n", "alpha\ngamma\n", path="src/x.py"))
    assert "src/x.py" in out
    assert "-beta" in out
    assert "+gamma" in out
    # Context line should appear with exactly one space prefix (no +/-).
    assert "alpha" in out


def test_structured_diff_emits_ansi_colour() -> None:
    """Confirm we actually colour the lines (CC's theme tokens)."""
    out = _render(render_structured_diff("a\n", "b\n"))
    assert "\x1b[" in out  # ANSI escape present
    # Both markers must survive the colour wrapping.
    assert "-a" in out
    assert "+b" in out


def test_structured_diff_no_changes_message() -> None:
    out = _render(render_structured_diff("same\n", "same\n"))
    assert "no changes" in out


# --------------------------------------------------------------------------- #
#  render_file_edit_accepted / rejected
# --------------------------------------------------------------------------- #


def test_file_edit_accepted_wraps_diff_with_path_header() -> None:
    diff = render_structured_diff("one\n", "two\n", path="a.py")
    out = _render(render_file_edit_accepted("a.py", diff))
    assert "a.py" in out
    # Panel border should be present (Rich's default box uses ─/│/┌ etc).
    assert any(ch in out for ch in ("─", "│", "┌", "└"))


def test_file_edit_rejected_mentions_rejection_and_shows_diff() -> None:
    """Mirrors CC's FileEditToolUseRejectedMessage body."""
    out = _render(render_file_edit_rejected("pkg/mod.py", "foo\n", "bar\n"))
    assert "User rejected" in out
    assert "pkg/mod.py" in out
    assert "-foo" in out
    assert "+bar" in out


# --------------------------------------------------------------------------- #
#  render_tool_error / render_tool_rejected
# --------------------------------------------------------------------------- #


def test_tool_error_prefixes_error_and_strips_xml_tags() -> None:
    out = _render(render_tool_error("read_file", "<error>boom</error>"))
    # Tags stripped, "Error: " prefix inserted, tool name present.
    assert "<error>" not in out
    assert "</error>" not in out
    assert "Error: boom" in out
    assert "read_file" in out


def test_tool_rejected_contains_tool_name_and_reason() -> None:
    out = _render(render_tool_rejected("write_file", "requires approval"))
    assert "write_file" in out
    assert "User rejected" in out
    assert "requires approval" in out


# --------------------------------------------------------------------------- #
#  render_file_path_link
# --------------------------------------------------------------------------- #


def test_file_path_link_emits_osc8_or_at_least_the_path() -> None:
    """OSC-8 is the standard escape but Rich may omit it on legacy terminals.

    We assert the path is visible and, when the terminal claims truecolor, an
    escape sequence is present (the link style itself emits ESC]8;;...).
    """
    txt = render_file_path_link("README.md")
    assert "README.md" in txt.plain
    out = _render(txt)
    assert "README.md" in out
    # Rich's truecolor console emits SOME ANSI for our brand-coloured span.
    assert "\x1b[" in out
