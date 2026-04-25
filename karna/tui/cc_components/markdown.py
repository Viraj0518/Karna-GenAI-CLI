"""Markdown + code-highlighting renderers, ported from upstream reference.

Mirrors the semantics of:
    upstream reference component
    upstream reference component
    upstream reference component
    upstream reference component
    upstream reference        (formatToken, configureMarked)
    upstream reference    (language detection by extension)
    upstream reference       (OSC-8 hyperlinks)

upstream uses ``marked`` + ``cli-highlight`` (highlight.js) under Ink. We re-skin
the same behavior on top of ``rich``: fenced code blocks get ``Syntax``,
tables get ``Table`` with bold headers + alternating row tint, links
preserve OSC-8 hyperlinks when the terminal supports them, and inline
code renders with a dim-background style.

This module is library-only — no REPL wiring.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Sequence

from rich.console import Group, RenderableType
from rich.markdown import Markdown as RichMarkdown
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

# ---------------------------------------------------------------------------
# Language detection — mirrors upstream's `extname(filePath).slice(1)` + cli-highlight
# alias lookup. highlight.js (which cli-highlight wraps) carries its own
# extension→language aliases (e.g. "py"→python, "rs"→rust, "ts"→typescript).
# Pygments does the same internally via `get_lexer_by_name`, so in most cases
# we just pass the extension through. For extensions that highlight.js
# recognizes but Pygments doesn't (or vice versa), we keep a small override
# table below — matches upstream's behavior of falling back to a plain lexer on
# "Unknown language" rather than failing the render.
# ---------------------------------------------------------------------------

# Explicit upstream/highlight.js-style aliases that Pygments handles differently
# or where upstream's expectation is unambiguous. Keys are lowercased.
_LANGUAGE_ALIASES: dict[str, str] = {
    # shells
    "sh": "bash",
    "bash": "bash",
    "zsh": "bash",
    "fish": "fish",
    # js/ts family
    "js": "javascript",
    "jsx": "jsx",
    "mjs": "javascript",
    "cjs": "javascript",
    "ts": "typescript",
    "tsx": "tsx",
    # python
    "py": "python",
    "pyi": "python",
    "pyw": "python",
    # rust / go
    "rs": "rust",
    "go": "go",
    # c family
    "c": "c",
    "h": "c",
    "cc": "cpp",
    "cpp": "cpp",
    "cxx": "cpp",
    "hpp": "cpp",
    "hh": "cpp",
    "m": "objectivec",
    "mm": "objectivec",
    # jvm
    "java": "java",
    "kt": "kotlin",
    "kts": "kotlin",
    "scala": "scala",
    "groovy": "groovy",
    # systems / scripting
    "rb": "ruby",
    "php": "php",
    "pl": "perl",
    "lua": "lua",
    "r": "r",
    "dart": "dart",
    "swift": "swift",
    "ex": "elixir",
    "exs": "elixir",
    "erl": "erlang",
    "hs": "haskell",
    "clj": "clojure",
    "cljs": "clojure",
    "ml": "ocaml",
    "fs": "fsharp",
    "nim": "nim",
    "zig": "zig",
    "v": "v",
    # web
    "html": "html",
    "htm": "html",
    "xml": "xml",
    "svg": "xml",
    "css": "css",
    "scss": "scss",
    "sass": "sass",
    "less": "less",
    "vue": "vue",
    # data / config
    "json": "json",
    "json5": "json",
    "yaml": "yaml",
    "yml": "yaml",
    "toml": "toml",
    "ini": "ini",
    "cfg": "ini",
    "conf": "ini",
    "env": "bash",
    "dockerfile": "docker",
    # docs / query
    "md": "markdown",
    "markdown": "markdown",
    "rst": "rst",
    "tex": "tex",
    "sql": "sql",
    "graphql": "graphql",
    "gql": "graphql",
    # build
    "makefile": "makefile",
    "mk": "makefile",
    "cmake": "cmake",
    "gradle": "gradle",
    # misc
    "diff": "diff",
    "patch": "diff",
    "proto": "protobuf",
    "tf": "terraform",
    "hcl": "hcl",
}

# Filenames (no extension) that map to a specific language — mirrors
# highlight.js's filename-based detection.
_FILENAME_LANGUAGES: dict[str, str] = {
    "dockerfile": "docker",
    "makefile": "makefile",
    "cmakelists.txt": "cmake",
    "jenkinsfile": "groovy",
    "rakefile": "ruby",
    "gemfile": "ruby",
    "vagrantfile": "ruby",
}


def detect_language_from_path(path: str) -> str:
    """Infer a language name from a file path.

    Mirrors upstream's ``extname(filePath).slice(1)`` + highlight.js alias lookup.
    Returns a language name suitable for ``rich.syntax.Syntax`` (i.e. a
    Pygments-accepted lexer name/alias), falling back to ``"text"`` when the
    extension is unknown — same spirit as upstream's "falling back to markdown".
    """
    if not path:
        return "text"
    base = os.path.basename(path).lower()

    # filename-only match (Dockerfile, Makefile, …)
    if base in _FILENAME_LANGUAGES:
        return _FILENAME_LANGUAGES[base]

    # double-extension like foo.tar.gz — not relevant, highlight on last ext
    _, ext = os.path.splitext(base)
    ext = ext.lstrip(".")
    if not ext:
        # e.g. a path like "Makefile" with no dot — already handled above,
        # but a bare unknown file becomes text.
        return "text"

    return _LANGUAGE_ALIASES.get(ext, ext)


# ---------------------------------------------------------------------------
# Code highlighting
# ---------------------------------------------------------------------------


def highlight_code(
    source: str,
    language: str | None,
    *,
    theme: str = "ansi_dark",
    line_numbers: bool = False,
    background_color: str | None = "default",
    word_wrap: bool = False,
) -> Syntax:
    """Syntax-highlight ``source`` as ``language``.

    upstream's ``HighlightedCode`` path: try the requested language, fall back to
    a plain lexer on "Unknown language" rather than raising. We replicate
    that by catching ``ClassNotFound`` from Pygments and re-issuing with
    ``"text"``.
    """
    lang = (language or "text").strip().lower() or "text"
    # honor the same alias table — callers often pass a bare extension
    lang = _LANGUAGE_ALIASES.get(lang, lang)

    # Resolve the lexer up front so we can fall back on "Unknown language".
    # Rich's `Syntax(...)` silently sets `lexer=None` for unknown names and
    # then renders the source as plain text — functional, but it hides the
    # fallback and makes the return value ambiguous. Mirror upstream's explicit
    # "log + fall back to markdown/plaintext" path from Fallback.tsx.
    try:
        from pygments.lexers import get_lexer_by_name
        from pygments.util import ClassNotFound

        try:
            get_lexer_by_name(lang)
        except ClassNotFound:
            lang = "text"
    except Exception:  # pragma: no cover — pygments missing
        lang = "text"

    return Syntax(
        source,
        lang,
        theme=theme,
        line_numbers=line_numbers,
        background_color=background_color,
        word_wrap=word_wrap,
    )


# ---------------------------------------------------------------------------
# Tables — mirrors MarkdownTable.tsx: bold headers, dim borders,
# alternating row tint for readability on dense tables.
# ---------------------------------------------------------------------------

_ROW_TINT = "on grey11"  # subtle zebra — matches Ink's dim-background look


def render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    title: str | None = None,
    zebra: bool = True,
) -> Table:
    """Build a Rich ``Table`` that mirrors upstream's MarkdownTable look.

    - Header cells are bold.
    - Borders use the ``heavy_head`` box — same visual weight as upstream's
      ``┌┬┐ ├┼┤ └┴┘`` borders for the header row.
    - Data rows alternate a dim-background tint when ``zebra`` is true.
    - Cell content is wrapped rather than truncated (upstream does the same via
      ``wrapAnsi`` — see MarkdownTable.tsx's ``wrapText``).
    """
    from rich import box

    table = Table(
        title=title,
        box=box.HEAVY_HEAD,
        header_style="bold",
        show_lines=False,
        expand=False,
        pad_edge=True,
    )
    for h in headers:
        table.add_column(str(h), overflow="fold", no_wrap=False)

    for idx, row in enumerate(rows):
        cells = [str(c) for c in row]
        if zebra and idx % 2 == 1:
            table.add_row(*cells, style=_ROW_TINT)
        else:
            table.add_row(*cells)
    return table


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

# Fence detection — same shape as marked's code-block token. Matches
# ```lang\n...\n``` and ~~~lang\n...\n~~~ (GFM). Language is optional.
_FENCE_RE = re.compile(
    r"^(?P<fence>```|~~~)[ \t]*(?P<lang>[\w.+#-]*)[ \t]*\n(?P<body>.*?)(?:\n)?^(?P=fence)[ \t]*$",
    re.MULTILINE | re.DOTALL,
)

# GFM table detection — a header row, a separator row, then >=1 body rows.
_TABLE_RE = re.compile(
    r"(?:^|\n)"
    r"(?P<header>\|[^\n]+\|)[ \t]*\n"
    r"(?P<sep>\|[\s:|\-]+\|)[ \t]*\n"
    r"(?P<body>(?:\|[^\n]+\|[ \t]*(?:\n|$))+)"
)


def _osc8(url: str, text: str | None = None) -> str:
    """OSC-8 hyperlink. Mirrors upstream reference.

    Terminals that don't understand OSC-8 will strip it silently on most
    modern emulators; rich additionally guards with ``Console.is_terminal``.
    We emit the escape unconditionally — matching upstream, which guards on
    ``supportsHyperlinks()`` at the ink layer. Callers who need that guard
    can wrap the returned Markdown renderable themselves.
    """
    display = text if text is not None else url
    return f"\x1b]8;;{url}\x07{display}\x1b]8;;\x07"


def _split_tables(text: str) -> list[tuple[str, str]]:
    """Return ``[(kind, chunk), ...]`` where kind is ``'md'`` or ``'table'``."""
    out: list[tuple[str, str]] = []
    pos = 0
    for m in _TABLE_RE.finditer(text):
        if m.start() > pos:
            out.append(("md", text[pos : m.start()]))
        out.append(("table", m.group(0).strip("\n")))
        pos = m.end()
    if pos < len(text):
        out.append(("md", text[pos:]))
    return out


def _split_fences(text: str) -> list[tuple[str, str, str | None]]:
    """Return ``[(kind, body, lang), ...]`` where kind is ``'md'`` or ``'code'``."""
    out: list[tuple[str, str, str | None]] = []
    pos = 0
    for m in _FENCE_RE.finditer(text):
        if m.start() > pos:
            out.append(("md", text[pos : m.start()], None))
        lang = (m.group("lang") or "").strip() or None
        out.append(("code", m.group("body"), lang))
        pos = m.end()
    if pos < len(text):
        out.append(("md", text[pos:], None))
    return out


def _parse_table_block(block: str) -> tuple[list[str], list[list[str]]]:
    """Parse a GFM table block into ``(headers, rows)``."""
    lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
    if len(lines) < 2:
        return [], []

    def _cells(line: str) -> list[str]:
        # Drop the leading/trailing pipe then split — matches marked.
        inner = line.strip()
        if inner.startswith("|"):
            inner = inner[1:]
        if inner.endswith("|"):
            inner = inner[:-1]
        return [c.strip() for c in inner.split("|")]

    headers = _cells(lines[0])
    rows = [_cells(ln) for ln in lines[2:]]
    # Pad short rows so column count matches header (upstream tolerates this too)
    for r in rows:
        while len(r) < len(headers):
            r.append("")
    return headers, rows


def render_markdown(
    text: str,
    *,
    code_theme: str = "ansi_dark",
    hyperlinks: bool | None = None,
) -> RenderableType:
    """Render Markdown ``text`` as a Rich renderable.

    Semantics mirror upstream's ``<Markdown>`` component:

    - Fenced code blocks become ``Syntax`` with the requested language
      (falling back to ``text`` on unknown languages — same as
      ``HighlightedCode/Fallback.tsx``).
    - GFM tables become ``rich.table.Table`` via :func:`render_table`,
      matching ``MarkdownTable.tsx``'s bold-header + zebra-tint look.
    - Everything else passes through ``rich.markdown.Markdown`` which
      handles headings, lists, blockquotes, emphasis, inline code, links.
    - OSC-8 hyperlinks are preserved by ``rich.markdown.Markdown`` when
      the terminal advertises support (``hyperlinks=True``).

    ``hyperlinks``: if ``None``, auto-detect from ``stdout`` (honoring
    the ``FORCE_HYPERLINK``/``NO_COLOR`` conventions). Set explicitly to
    force on/off.
    """
    if hyperlinks is None:
        hyperlinks = _detect_hyperlink_support()

    renderables: list[RenderableType] = []

    # Two-pass split: first peel out fenced code blocks (their contents
    # mustn't be re-parsed for tables or markdown), then split the remaining
    # prose chunks on GFM tables.
    for kind, chunk, lang in _split_fences(text):
        if kind == "code":
            renderables.append(highlight_code(chunk, lang, theme=code_theme))
            continue

        for sub_kind, sub_chunk in _split_tables(chunk):
            if sub_kind == "table":
                headers, rows = _parse_table_block(sub_chunk)
                if headers:
                    renderables.append(render_table(headers, rows))
                    continue
                # fall through as plain markdown if parse failed
                sub_chunk = sub_chunk

            stripped = sub_chunk.strip("\n")
            if not stripped.strip():
                continue
            renderables.append(
                RichMarkdown(
                    stripped,
                    code_theme=code_theme,
                    hyperlinks=hyperlinks,
                    inline_code_theme=code_theme,
                    inline_code_lexer="text",
                )
            )

    if not renderables:
        return Text("")
    if len(renderables) == 1:
        return renderables[0]
    return Group(*renderables)


def _detect_hyperlink_support() -> bool:
    """Best-effort OSC-8 support probe.

    Mirrors the upstream project/ink/supports-hyperlinks.ts's logic at a high
    level: honor ``FORCE_HYPERLINK`` / ``NO_COLOR``, require a TTY, and
    allow an opt-in for known-good terminals via ``TERM_PROGRAM``.
    """
    force = os.environ.get("FORCE_HYPERLINK")
    if force is not None:
        return force not in ("0", "false", "")
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    if term_program in {
        "iterm.app",
        "wezterm",
        "vscode",
        "hyper",
        "tabby",
        "ghostty",
    }:
        return True
    # Windows Terminal
    if os.environ.get("WT_SESSION"):
        return True
    # conservative default
    return False


__all__ = [
    "detect_language_from_path",
    "highlight_code",
    "render_markdown",
    "render_table",
]
