"""Tests for karna.tui.output_style — all five builtin styles + protocol."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from karna.tui.output_style import (
    BUILTIN_STYLES,
    OutputStyle,
    active_style_name,
    get_style,
)


def _render(obj) -> str:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=120, color_system="truecolor")
    console.print(obj)
    return buf.getvalue()


def test_all_five_builtins_load() -> None:
    expected = {"default", "minimal", "verbose", "compact", "dark-code"}
    assert expected == set(BUILTIN_STYLES.keys())


def test_each_style_implements_protocol() -> None:
    for name, style in BUILTIN_STYLES.items():
        assert isinstance(style, OutputStyle), name
        # Both required methods should be callable and return something renderable.
        a = style.format_assistant("hello")
        b = style.format_tool_header("bash", {"cmd": "ls"})
        assert a is not None
        assert b is not None
        # Rich should be able to print them without error.
        _render(a)
        _render(b)


def test_get_style_returns_known() -> None:
    s = get_style("minimal")
    assert s.name == "minimal"


def test_get_style_unknown_returns_default() -> None:
    s = get_style("does-not-exist")
    assert s.name == "default"


def test_get_style_nonexistent_doesnt_raise() -> None:
    # Must not raise for any string, including weird ones.
    for bad in ["", "  ", "NONE", "default ", "\n"]:
        s = get_style(bad)
        assert s is not None


def test_verbose_style_includes_timestamp() -> None:
    out = BUILTIN_STYLES["verbose"].format_assistant("hi")
    rendered = _render(out)
    # ISO timestamp starts with the year.
    assert "20" in rendered
    assert "hi" in rendered


def test_compact_style_flattens_newlines() -> None:
    out = BUILTIN_STYLES["compact"].format_assistant("line one\nline two")
    rendered = _render(out).strip()
    # The rendered text should not contain a hard newline between the phrases.
    assert "line one line two" in " ".join(rendered.split())


def test_active_style_name_missing_file(tmp_path: Path) -> None:
    assert active_style_name(tmp_path / "nope.toml") == "default"


def test_active_style_name_reads_toml(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('[tui]\noutput_style = "minimal"\n', encoding="utf-8")
    assert active_style_name(cfg) == "minimal"


def test_active_style_name_invalid_falls_back(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('[tui]\noutput_style = "bogus"\n', encoding="utf-8")
    assert active_style_name(cfg) == "default"
