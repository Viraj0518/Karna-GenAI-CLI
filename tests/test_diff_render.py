"""Tests for karna.tui.diff — unified, side-by-side, and file-edit renderers."""

from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.table import Table

from karna.tui.diff import (
    render_file_edit,
    render_side_by_side,
    render_unified_diff,
)


def _render(console: Console, obj) -> str:
    console.print(obj)
    return console.file.getvalue()


# --------------------------------------------------------------------------- #
#  unified_diff
# --------------------------------------------------------------------------- #


def test_unified_diff_contains_plus_and_minus_markers() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=120, color_system="truecolor")
    out = render_unified_diff("hello\nworld\n", "hello\nearth\n", path="x.txt")
    output = _render(console, out)
    # The raw diff characters are visible even with coloring (ANSI wraps them).
    assert "-world" in output
    assert "+earth" in output


def test_unified_diff_color_codes_added_vs_removed() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=120, color_system="truecolor")
    out = render_unified_diff("a\n", "b\n")
    output = _render(console, out)
    # Green (success) and red (danger) ANSI should both appear.
    # Using simple substring: ANSI ESC sequence starts with \x1b[
    assert "\x1b[" in output
    assert "-a" in output
    assert "+b" in output


def test_unified_diff_no_changes_message() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    out = render_unified_diff("same\n", "same\n")
    output = _render(console, out)
    assert "no changes" in output


# --------------------------------------------------------------------------- #
#  side-by-side
# --------------------------------------------------------------------------- #


def test_side_by_side_returns_table() -> None:
    t = render_side_by_side("a\nb\n", "a\nc\n")
    assert isinstance(t, Table)
    # Two columns: before and after
    assert len(t.columns) == 2
    assert t.columns[0].header == "before"
    assert t.columns[1].header == "after"


def test_side_by_side_shows_both_sides() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=200)
    t = render_side_by_side("foo\nbar\n", "foo\nbaz\n")
    output = _render(console, t)
    assert "foo" in output
    assert "bar" in output
    assert "baz" in output


# --------------------------------------------------------------------------- #
#  file edit (header + stats)
# --------------------------------------------------------------------------- #


def test_file_edit_header_includes_path_and_stats() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=120, color_system="truecolor")
    out = render_file_edit(
        "src/hello.py",
        "print('hi')\n",
        "print('hello')\nprint('world')\n",
        mode="unified",
    )
    output = _render(console, out)
    assert "src/hello.py" in output
    assert "+2" in output  # two added lines
    assert "-1" in output  # one removed line


def test_file_edit_side_by_side_mode() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=200)
    out = render_file_edit(
        "a.py",
        "one\ntwo\n",
        "one\ntwo_prime\n",
        mode="side-by-side",
    )
    output = _render(console, out)
    assert "a.py" in output
    assert "two" in output
