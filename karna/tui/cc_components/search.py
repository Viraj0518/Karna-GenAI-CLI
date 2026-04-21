"""Search, history, and quick-open pickers — ported from Claude Code.

Mirrors the UX of Claude Code's ``HistorySearchDialog.tsx``,
``GlobalSearchDialog.tsx``, ``QuickOpenDialog.tsx``, ``SearchBox.tsx``, and
``TagTabs.tsx`` — but rewritten for Nellie's prompt_toolkit stack.

Design notes
------------
* **Inline dialogs.** These never take over the screen. They render above
  the input line via ``prompt_toolkit.patch_stdout.patch_stdout()`` — the
  same pattern Hermes (``hermes_repl.py``) uses for its REPL. Each picker
  is an ``Application`` with ``full_screen=False`` so scrollback stays
  intact.
* **Brand color.** The focused row highlight uses Nellie's
  ``#3C73BD`` from :mod:`karna.tui.design_tokens`, wrapped into a
  prompt_toolkit style.
* **Keyboard hints.** Each dialog renders a one-line hint bar at the
  bottom (e.g. ``enter  select   esc  cancel``).
* **Fuzzy match.** :func:`history_search` ranks exact-substring hits
  first, then subsequence (character-order) hits — identical to CC's
  ``isSubsequence`` fallback. :func:`quick_open_file` prefers
  ``rapidfuzz`` when available (faster + scored) and falls back to a
  pure-Python substring-and-subsequence ranker with the same ordering.
* **Global search.** Routes through :class:`karna.sessions.db.SessionDB`'s
  FTS5 ``search()`` method — no new schema needed.

This module is event-loop-aware: every dialog is an ``async`` function
that returns when the user selects a row or cancels with ``esc`` /
``ctrl-c``.

Upstream references — see NOTICES.md for attribution.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Sequence

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style

from karna.models import Message
from karna.tui.design_tokens import SEMANTIC

if TYPE_CHECKING:
    from karna.sessions.db import SessionDB

# Optional: rapidfuzz gives ~10x faster fuzzy file picking with a real score.
try:
    from rapidfuzz import fuzz as _rf_fuzz
    from rapidfuzz import process as _rf_process

    _HAS_RAPIDFUZZ = True
except ImportError:  # pragma: no cover - optional dep
    _rf_fuzz = None
    _rf_process = None
    _HAS_RAPIDFUZZ = False


BRAND = SEMANTIC.get("accent.brand", "#3C73BD")
MUTED = SEMANTIC.get("text.secondary", "#A0A4AD")
SUBTLE = SEMANTIC.get("text.tertiary", "#5F6472")

_MAX_VISIBLE_ROWS = 10


# --------------------------------------------------------------------------- #
#  Fuzzy helpers
# --------------------------------------------------------------------------- #


def _is_subsequence(text: str, query: str) -> bool:
    """Return True if every char of *query* appears in *text* in order.

    Mirrors CC's ``isSubsequence`` tail in
    ``HistorySearchDialog.tsx`` / ``QuickOpenDialog.tsx``.
    """
    j = 0
    for ch in text:
        if j >= len(query):
            break
        if ch == query[j]:
            j += 1
    return j == len(query)


def _rank_strings(items: Sequence[str], query: str) -> list[tuple[int, str]]:
    """Order *items* by match quality against *query*.

    Returns a list of ``(original_index, item)`` tuples. Exact substring
    matches come first, subsequence matches second. When *query* is empty,
    items are returned in their original order.
    """
    if not query:
        return list(enumerate(items))

    q = query.lower()

    if _HAS_RAPIDFUZZ:
        # rapidfuzz: score every item, keep > 0, preserve original order on ties.
        scored: list[tuple[int, int, str]] = []
        for idx, item in enumerate(items):
            score = _rf_fuzz.partial_ratio(q, item.lower())  # type: ignore[union-attr]
            if score > 0:
                scored.append((-score, idx, item))
        scored.sort()
        return [(idx, item) for _, idx, item in scored]

    exact: list[tuple[int, str]] = []
    fuzzy: list[tuple[int, str]] = []
    for idx, item in enumerate(items):
        lo = item.lower()
        if q in lo:
            exact.append((idx, item))
        elif _is_subsequence(lo, q):
            fuzzy.append((idx, item))
    return exact + fuzzy


# --------------------------------------------------------------------------- #
#  Visual primitives
# --------------------------------------------------------------------------- #


def render_search_box(placeholder: str, value: str) -> FormattedText:
    """Return prompt_toolkit ``FormattedText`` for the search input line.

    Matches CC's ``SearchBox.tsx`` visual: a `⌕` prefix, dimmed placeholder
    when empty, bright text when filled. The value is rendered with a
    brand-colored caret suffix.
    """
    fragments: list[tuple[str, str]] = [("class:prefix", "\u2315 ")]
    if value:
        fragments.append(("class:value", value))
        fragments.append(("class:caret", "\u2588"))
    else:
        fragments.append(("class:placeholder", placeholder))
    return FormattedText(fragments)


class TagTabs:
    """Horizontal tab bar for filtering results by category/tag.

    Render-only — the caller drives selection. Mirrors CC's ``TagTabs.tsx``
    but emits prompt_toolkit ``FormattedText`` instead of Ink ``Text``.

    Example::

        bar = TagTabs(["All", "files", "prompts"], selected_index=1)
        ft = bar.render(available_width=80)

    Use ``next_tab()`` / ``prev_tab()`` to advance the selection.
    """

    ALL_LABEL = "All"

    def __init__(
        self,
        tabs: Sequence[str],
        *,
        selected_index: int = 0,
        show_all_projects: bool = False,
    ) -> None:
        if not tabs:
            raise ValueError("TagTabs requires at least one tab")
        self.tabs: list[str] = list(tabs)
        self.selected_index = max(0, min(selected_index, len(self.tabs) - 1))
        self.show_all_projects = show_all_projects

    # ------------------------------------------------------------------ #
    #  Navigation
    # ------------------------------------------------------------------ #

    def next_tab(self) -> None:
        self.selected_index = (self.selected_index + 1) % len(self.tabs)

    def prev_tab(self) -> None:
        self.selected_index = (self.selected_index - 1) % len(self.tabs)

    def current(self) -> str:
        return self.tabs[self.selected_index]

    # ------------------------------------------------------------------ #
    #  Rendering
    # ------------------------------------------------------------------ #

    def render(self, available_width: int = 80) -> FormattedText:
        """Return a single-line ``FormattedText`` for the tab bar.

        Truncates the visible window around ``selected_index`` to fit
        ``available_width`` — matching CC's windowing behavior.
        """
        resume_label = (
            "Resume (All Projects)" if self.show_all_projects else "Resume"
        )
        hint = "(tab to cycle)"
        # Budget for the tabs portion itself.
        budget = max(0, available_width - len(resume_label) - len(hint) - 4)

        fragments: list[tuple[str, str]] = [
            ("class:tab.resume", resume_label),
            ("", " "),
        ]

        # Build the visible window, centered on selected_index.
        start = 0
        end = len(self.tabs)
        widths = [self._tab_width(t) for t in self.tabs]
        total = sum(widths) + max(0, len(widths) - 1)  # gaps
        if total > budget and budget > 0:
            start = self.selected_index
            end = self.selected_index + 1
            used = widths[self.selected_index]
            while start > 0 or end < len(self.tabs):
                grew = False
                if start > 0 and used + widths[start - 1] + 1 <= budget:
                    start -= 1
                    used += widths[start] + 1
                    grew = True
                if end < len(self.tabs) and used + widths[end] + 1 <= budget:
                    used += widths[end] + 1
                    end += 1
                    grew = True
                if not grew:
                    break

        if start > 0:
            fragments.append(("class:tab.arrow", f"\u2190 {start} "))

        for i in range(start, end):
            tab = self.tabs[i]
            label = tab if tab == self.ALL_LABEL else f"#{tab}"
            if i == self.selected_index:
                fragments.append(("class:tab.selected", f" {label} "))
            else:
                fragments.append(("class:tab", f" {label} "))

        hidden_right = len(self.tabs) - end
        if hidden_right > 0:
            fragments.append(("class:tab.arrow", f" \u2192{hidden_right} {hint}"))
        else:
            fragments.append(("class:tab.arrow", f" {hint}"))

        return FormattedText(fragments)

    def _tab_width(self, tab: str) -> int:
        # " #tag " or " All "
        return (len(tab) + 2) + (0 if tab == self.ALL_LABEL else 1)


# --------------------------------------------------------------------------- #
#  Dialog runner (shared by all three pickers)
# --------------------------------------------------------------------------- #


def _style() -> Style:
    return Style.from_dict(
        {
            "prefix": f"{MUTED}",
            "value": "noinherit",
            "placeholder": f"{SUBTLE}",
            "caret": f"{BRAND} bold",
            "row": "noinherit",
            "row.focused": f"{BRAND} bold reverse",
            "hint": f"{SUBTLE}",
            "title": f"{BRAND} bold",
            "tab": "noinherit",
            "tab.selected": f"{BRAND} bold reverse",
            "tab.resume": f"{BRAND} bold",
            "tab.arrow": f"{SUBTLE}",
        }
    )


async def _run_picker(
    *,
    title: str,
    placeholder: str,
    initial_query: str,
    fetch_items,
    render_row,
    hint: str = "enter select   esc cancel",
) -> int | None:
    """Run an inline picker dialog. Returns the index of the chosen item,
    or ``None`` on cancel.

    *fetch_items(query)* must return a list of ranked rows.
    *render_row(row, focused)* must return a ``FormattedText`` fragment
    list for a single row.
    """
    query = [initial_query]
    focused = [0]
    items_cache: list = list(fetch_items(initial_query))

    def _refresh() -> None:
        items_cache[:] = fetch_items(query[0])
        focused[0] = max(0, min(focused[0], max(0, len(items_cache) - 1)))

    # --- Search input (single-line buffer) ---
    buf = Buffer(multiline=False)
    buf.text = initial_query

    def _on_query_change(_: Buffer) -> None:
        query[0] = buf.text
        _refresh()

    buf.on_text_changed += _on_query_change

    # --- Row renderer ---
    def _rows_ft() -> FormattedText:
        out: list[tuple[str, str]] = []
        if not items_cache:
            out.append(("class:hint", "  (no matches)\n"))
            return FormattedText(out)
        start = 0
        end = min(len(items_cache), _MAX_VISIBLE_ROWS)
        # Window follows focus.
        if focused[0] >= end:
            end = focused[0] + 1
            start = max(0, end - _MAX_VISIBLE_ROWS)
        for i in range(start, end):
            row = items_cache[i]
            out.extend(render_row(row, i == focused[0]))
            out.append(("", "\n"))
        return FormattedText(out)

    def _header_ft() -> FormattedText:
        return FormattedText(
            [("class:title", f"{title}\n")]
        )

    def _input_ft() -> FormattedText:
        return render_search_box(placeholder, query[0])

    def _hint_ft() -> FormattedText:
        return FormattedText([("class:hint", f"  {hint}")])

    # --- Keybindings ---
    kb = KeyBindings()
    result: dict[str, int | None] = {"idx": None}

    @kb.add("up")
    def _(event):  # noqa: ANN001
        if items_cache:
            focused[0] = (focused[0] - 1) % len(items_cache)

    @kb.add("down")
    def _(event):  # noqa: ANN001
        if items_cache:
            focused[0] = (focused[0] + 1) % len(items_cache)

    @kb.add("enter")
    def _(event):  # noqa: ANN001
        if items_cache:
            result["idx"] = focused[0]
        event.app.exit()

    @kb.add("escape", eager=True)
    @kb.add("c-c")
    def _(event):  # noqa: ANN001
        result["idx"] = None
        event.app.exit()

    # --- Layout ---
    layout = Layout(
        HSplit(
            [
                Window(FormattedTextControl(_header_ft), height=1),
                Window(BufferControl(buffer=buf), height=1),
                Window(FormattedTextControl(_rows_ft)),
                Window(FormattedTextControl(_hint_ft), height=1),
            ]
        )
    )

    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        style=_style(),
        full_screen=False,
        mouse_support=False,
    )

    try:
        with patch_stdout():
            await app.run_async()
    except (EOFError, KeyboardInterrupt):
        return None

    return result["idx"]


# --------------------------------------------------------------------------- #
#  Public API
# --------------------------------------------------------------------------- #


async def history_search(
    session_messages: list[Message],
    *,
    query: str = "",
) -> Message | None:
    """Ctrl-R-style search across messages in the current session.

    Mirrors CC's ``HistorySearchDialog.tsx``:

    * Filters *session_messages* as the user types (exact substring first,
      subsequence fallback).
    * Returns the selected :class:`Message` on enter, or ``None`` on esc.

    Only user messages with textual content are surfaced — assistant
    responses and tool results are skipped, same as CC's prompt history.
    """
    # Pre-filter to user prompts with content. Preserve chronological order.
    candidates: list[Message] = [
        m for m in session_messages if m.role == "user" and (m.content or "").strip()
    ]

    def _fetch(q: str) -> list[Message]:
        if not q:
            return list(candidates)
        ranked = _rank_strings([m.content for m in candidates], q)
        return [candidates[idx] for idx, _ in ranked]

    def _render(row: Message, focused: bool) -> list[tuple[str, str]]:
        style = "class:row.focused" if focused else "class:row"
        first = row.content.splitlines()[0] if row.content else ""
        return [(style, f"  {first[:80]}")]

    idx = await _run_picker(
        title="Search prompts",
        placeholder="Filter history\u2026",
        initial_query=query,
        fetch_items=_fetch,
        render_row=_render,
    )
    if idx is None:
        return None
    items = _fetch(query)
    if 0 <= idx < len(items):
        return items[idx]
    return None


async def global_search(
    store: "SessionDB",
    *,
    query: str = "",
) -> tuple[str, Message] | None:
    """Cmd-Shift-F-style search across *all* sessions via SQLite FTS5.

    Uses ``SessionDB.search()`` which wraps the ``messages_fts`` virtual
    table. Returns ``(session_id, Message)`` or ``None`` on cancel.
    """

    def _fetch(q: str):  # list[tuple[str, Message, str]]
        if not q.strip():
            return []
        try:
            rows = store.search(_fts5_escape(q), limit=50)
        except Exception:
            return []
        out: list[tuple[str, Message, str]] = []
        for row in rows:
            msg = Message(role=row.get("role", "user"), content=row.get("content") or "")
            out.append((row["session_id"], msg, (row.get("content") or "")[:80]))
        return out

    def _render(row, focused: bool) -> list[tuple[str, str]]:
        sid, _msg, preview = row
        style = "class:row.focused" if focused else "class:row"
        return [(style, f"  {sid[:10]}  "), ("class:hint", f"{preview}")]

    idx = await _run_picker(
        title="Global search",
        placeholder="Filter all sessions\u2026",
        initial_query=query,
        fetch_items=_fetch,
        render_row=_render,
    )
    if idx is None:
        return None
    items = _fetch(query)
    if 0 <= idx < len(items):
        sid, msg, _ = items[idx]
        return sid, msg
    return None


async def quick_open_file(
    roots: list[Path],
    *,
    query: str = "",
) -> Path | None:
    """Cmd-P-style fuzzy file picker rooted at the given directories.

    Walks every root shallowly (skipping dotted directories, ``__pycache__``,
    ``node_modules``, ``.git``) and ranks results by fuzzy match against
    the path string. Returns the chosen :class:`Path` or ``None`` on cancel.

    Uses ``rapidfuzz.fuzz.partial_ratio`` when the dependency is
    installed, otherwise falls back to the substring+subsequence ranker.
    """
    files: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for p in _walk_files(root):
            if p not in seen:
                seen.add(p)
                files.append(p)
    relpaths = [_display_path(p, roots) for p in files]

    def _fetch(q: str) -> list[Path]:
        if not q:
            return list(files)
        ranked = _rank_strings(relpaths, q)
        return [files[idx] for idx, _ in ranked]

    def _render(row: Path, focused: bool) -> list[tuple[str, str]]:
        style = "class:row.focused" if focused else "class:row"
        return [(style, f"  {_display_path(row, roots)}")]

    idx = await _run_picker(
        title="Open file",
        placeholder="Filter files\u2026",
        initial_query=query,
        fetch_items=_fetch,
        render_row=_render,
    )
    if idx is None:
        return None
    items = _fetch(query)
    if 0 <= idx < len(items):
        return items[idx]
    return None


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #


_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache"}


def _fts5_escape(query: str) -> str:
    """Quote a user query so SQLite FTS5 treats it as a safe phrase.

    FTS5's MATCH grammar treats ``"``, ``*``, ``(``, ``)``, ``:`` and a
    handful of operator words (``AND``, ``OR``, ``NOT``, ``NEAR``) as
    reserved. A free-text query from a prompt_toolkit search box will
    routinely contain spaces, punctuation, and mixed case — wrapping the
    query in double-quotes (and escaping embedded quotes) makes FTS5
    interpret it as a single phrase token, which is what the dialog
    actually wants.
    """
    cleaned = query.strip().replace('"', '""')
    if not cleaned:
        return cleaned
    return f'"{cleaned}"'


def _walk_files(root: Path) -> Iterable[Path]:
    """Yield every file under *root*, skipping common junk dirs."""
    try:
        entries = list(root.iterdir())
    except (OSError, PermissionError):
        return
    for entry in entries:
        if entry.name.startswith(".") and entry.name not in {".env", ".gitignore"}:
            continue
        if entry.is_dir():
            if entry.name in _SKIP_DIRS:
                continue
            yield from _walk_files(entry)
        elif entry.is_file():
            yield entry


def _display_path(p: Path, roots: Sequence[Path]) -> str:
    """Return the shortest path expression relative to any of *roots*."""
    best: str = str(p)
    for root in roots:
        try:
            rel = p.relative_to(root)
            s = str(rel)
            if len(s) < len(best):
                best = s
        except ValueError:
            continue
    return best


# --------------------------------------------------------------------------- #
#  Re-exported fuzzy helper — tests import this directly.
# --------------------------------------------------------------------------- #


def fuzzy_match(items: Sequence[str], query: str) -> list[str]:
    """Return *items* ordered by match quality against *query*.

    Thin wrapper over :func:`_rank_strings` for library consumers that
    just want a ranked list without indices.
    """
    return [item for _, item in _rank_strings(items, query)]


__all__ = [
    "TagTabs",
    "fuzzy_match",
    "global_search",
    "history_search",
    "quick_open_file",
    "render_search_box",
]
