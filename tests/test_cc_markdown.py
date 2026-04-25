"""Tests for ``karna.tui.cc_components.markdown`` — the upstream-ported markdown
and code-highlighting renderers.

One test per exposed function, plus a couple of integration checks for the
semantic behaviors called out in the port spec (syntax highlighting of
fenced code, bold headers on tables, OSC-8 link preservation, dim-background
inline code).
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from karna.tui.cc_components.markdown import (
    detect_language_from_path,
    highlight_code,
    render_markdown,
    render_table,
)


def _render(renderable, *, width: int = 80, force_terminal: bool = False) -> str:
    buf = io.StringIO()
    console = Console(
        file=buf,
        width=width,
        force_terminal=force_terminal,
        color_system="truecolor" if force_terminal else None,
        legacy_windows=False,
    )
    console.print(renderable)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# detect_language_from_path
# --------------------------------------------------------------------------- #


class TestDetectLanguageFromPath:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("foo/bar.py", "python"),
            ("main.rs", "rust"),
            ("app.ts", "typescript"),
            ("Component.tsx", "tsx"),
            ("index.js", "javascript"),
            ("styles.css", "css"),
            ("README.md", "markdown"),
            ("data.json", "json"),
            ("script.sh", "bash"),
            ("query.sql", "sql"),
            ("Dockerfile", "docker"),
            ("Makefile", "makefile"),
        ],
    )
    def test_known_extensions(self, path, expected):
        assert detect_language_from_path(path) == expected

    def test_unknown_extension_passes_through_lowered(self):
        # upstream's behavior: hand the raw ext to highlight.js; on unknown it
        # falls back. We keep the ext for Pygments to attempt a match.
        assert detect_language_from_path("weird.madeupext") == "madeupext"

    def test_empty_path_returns_text(self):
        assert detect_language_from_path("") == "text"

    def test_no_extension_returns_text(self):
        assert detect_language_from_path("somefile") == "text"


# --------------------------------------------------------------------------- #
# highlight_code
# --------------------------------------------------------------------------- #


class TestHighlightCode:
    def test_returns_syntax_for_known_language(self):
        s = highlight_code("print('hello')", "python")
        assert isinstance(s, Syntax)
        assert s.lexer is not None
        assert s.lexer.name.lower() == "python"

    def test_alias_is_resolved(self):
        s = highlight_code("let x = 1;", "ts")
        assert isinstance(s, Syntax)
        assert "typescript" in s.lexer.name.lower()

    def test_unknown_language_falls_back_to_text(self):
        # Mirrors upstream's "Unknown language" catch — must not raise.
        s = highlight_code("whatever", "definitely-not-a-language")
        assert isinstance(s, Syntax)
        # Pygments' text lexer is called "Text only"
        assert "text" in s.lexer.name.lower()

    def test_none_language_is_safe(self):
        s = highlight_code("plain text", None)
        assert isinstance(s, Syntax)

    def test_rendering_produces_ansi_when_forced(self):
        s = highlight_code("def f(): pass", "python")
        out = _render(s, force_terminal=True)
        # Any ANSI escape present means Pygments coloring ran.
        assert "\x1b[" in out
        assert "def" in out


# --------------------------------------------------------------------------- #
# render_table
# --------------------------------------------------------------------------- #


class TestRenderTable:
    def test_returns_rich_table(self):
        t = render_table(["A", "B"], [["1", "2"], ["3", "4"]])
        assert isinstance(t, Table)
        assert len(t.columns) == 2
        # Header style is bold — mirrors MarkdownTable.tsx's ANSI_BOLD wrap
        assert t.header_style == "bold"

    def test_contents_render(self):
        t = render_table(
            ["Name", "Score"],
            [["alpha", "99"], ["beta", "42"], ["gamma", "7"]],
        )
        out = _render(t)
        for needle in ("Name", "Score", "alpha", "beta", "gamma", "99", "42", "7"):
            assert needle in out

    def test_zebra_tint_applied_to_odd_rows(self):
        t = render_table(
            ["X"],
            [["a"], ["b"], ["c"], ["d"]],
            zebra=True,
        )
        # Rich stores row styles on the Table; inspect the internal rows list.
        # Row 0 → no style, row 1 → tint, row 2 → no style, row 3 → tint.
        assert t.rows[0].style is None
        assert t.rows[1].style is not None
        assert t.rows[2].style is None
        assert t.rows[3].style is not None

    def test_zebra_off_leaves_rows_unstyled(self):
        t = render_table(["X"], [["a"], ["b"]], zebra=False)
        assert t.rows[0].style is None
        assert t.rows[1].style is None


# --------------------------------------------------------------------------- #
# render_markdown
# --------------------------------------------------------------------------- #


class TestRenderMarkdown:
    def test_plain_paragraph(self):
        out = _render(render_markdown("hello world"))
        assert "hello world" in out

    def test_heading_renders(self):
        out = _render(render_markdown("# Title\n\nbody"))
        assert "Title" in out
        assert "body" in out

    def test_inline_code_is_styled(self):
        # upstream uses a color('permission') wrap on codespan — we delegate to
        # rich.markdown which applies a dim-background style. Render with
        # force_terminal so the ANSI style escapes are emitted.
        out = _render(render_markdown("use `foo()` please"), force_terminal=True)
        assert "foo()" in out
        assert "\x1b[" in out  # some ANSI style was applied

    def test_fenced_code_uses_syntax_highlighter(self):
        md = "Example:\n\n```python\ndef f():\n    return 1\n```\n"
        out = _render(render_markdown(md), force_terminal=True)
        assert "def" in out
        assert "return" in out
        # Pygments emits ANSI colors for python keywords
        assert "\x1b[" in out

    def test_fenced_code_unknown_language_does_not_crash(self):
        md = "```not-a-language\nbody body\n```"
        out = _render(render_markdown(md))
        assert "body body" in out

    def test_gfm_table_is_promoted_to_rich_table(self):
        md = "before\n\n| H1 | H2 |\n|----|----|\n| a  | b  |\n| c  | d  |\n\nafter\n"
        out = _render(render_markdown(md))
        for needle in ("before", "after", "H1", "H2", "a", "b", "c", "d"):
            assert needle in out
        # heavy_head borders contain these box-drawing glyphs
        assert any(ch in out for ch in "━┃┏┓┗┛")

    def test_link_preserved_when_hyperlinks_on(self):
        md = "see [the docs](https://example.com/docs) for more"
        out = _render(
            render_markdown(md, hyperlinks=True),
            force_terminal=True,
        )
        # OSC-8 sequence opens with ESC ] 8 ; ;  — rich emits this when
        # hyperlinks=True. Matches upstream reference.
        assert "\x1b]8;" in out
        assert "example.com/docs" in out

    def test_link_without_hyperlink_support_still_shows_text(self):
        md = "see [docs](https://example.com) for more"
        out = _render(render_markdown(md, hyperlinks=False))
        assert "docs" in out
