"""Tests for the CC-ported search / history / quick-open dialogs.

Covers the pure helpers (fuzzy ranker, ``render_search_box``, ``TagTabs``)
and the SessionDB-backed ``global_search`` backend. We don't drive the
prompt_toolkit dialogs end-to-end — they require a real terminal — but
we do verify the data pipeline the interactive layer sits on top of.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from karna.models import Message
from karna.sessions.db import SessionDB
from karna.tui.cc_components import search as cc_search

# --------------------------------------------------------------------------- #
#  1. Fuzzy ranking — exact substring first, subsequence second
# --------------------------------------------------------------------------- #


def test_fuzzy_match_orders_exact_then_subsequence() -> None:
    items = [
        "totally unrelated text",
        "src/karna/tui/cc_components/search.py",
        "src/karna/tui/output.py",
        "src/Search_Engine.py",
    ]
    ranked = cc_search.fuzzy_match(items, "search")

    # "Search_Engine.py" and the cc_components path both contain "search"
    # (case-insensitive) so they must come before the unrelated row.
    assert "totally unrelated text" not in ranked[:2]
    assert any("search" in r.lower() for r in ranked[:2])
    # Empty query preserves the input order.
    assert cc_search.fuzzy_match(items, "") == items


def test_is_subsequence_fallback_catches_typos() -> None:
    # "scpy" is not a substring of "search.py" but IS a subsequence.
    assert cc_search._is_subsequence("search.py", "scpy") is True
    assert cc_search._is_subsequence("search.py", "zzzz") is False


# --------------------------------------------------------------------------- #
#  2. render_search_box — visual primitives
# --------------------------------------------------------------------------- #


def test_render_search_box_dimmed_placeholder_and_bright_value() -> None:
    empty = cc_search.render_search_box("Filter files\u2026", "")
    # Prefix (\u2315) + placeholder
    classes = {cls for cls, _ in empty}
    assert "class:prefix" in classes
    assert "class:placeholder" in classes
    assert "class:value" not in classes

    filled = cc_search.render_search_box("Filter files\u2026", "foo")
    filled_classes = {cls for cls, _ in filled}
    assert "class:value" in filled_classes
    assert "class:caret" in filled_classes  # block cursor suffix
    assert "class:placeholder" not in filled_classes


# --------------------------------------------------------------------------- #
#  3. TagTabs — tab bar widget
# --------------------------------------------------------------------------- #


def test_tag_tabs_cycles_and_marks_selection() -> None:
    bar = cc_search.TagTabs(["All", "files", "prompts"], selected_index=0)
    assert bar.current() == "All"
    bar.next_tab()
    assert bar.current() == "files"
    bar.next_tab()
    bar.next_tab()
    # Wraps around to All.
    assert bar.current() == "All"
    bar.prev_tab()
    assert bar.current() == "prompts"

    ft = bar.render(available_width=80)
    joined = "".join(text for _, text in ft)
    assert "Resume" in joined
    assert "#prompts" in joined  # non-All tabs get the hash prefix
    assert "All" in joined
    # Selected tab uses the brand-highlighted style.
    assert any(cls == "class:tab.selected" for cls, _ in ft)


def test_tag_tabs_empty_raises() -> None:
    with pytest.raises(ValueError):
        cc_search.TagTabs([])


# --------------------------------------------------------------------------- #
#  4. quick_open_file data layer — tmp tree
# --------------------------------------------------------------------------- #


def test_walk_files_skips_junk_dirs(tmp_path: Path) -> None:
    # Build: tmp/src/a.py, tmp/src/b.txt, tmp/node_modules/x.js (skipped),
    #        tmp/.git/HEAD (skipped), tmp/.env (kept — special-cased).
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("print(1)")
    (tmp_path / "src" / "b.txt").write_text("hi")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("x")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: main")
    (tmp_path / ".env").write_text("SECRET=1")

    files = sorted(str(p.name) for p in cc_search._walk_files(tmp_path))
    assert "a.py" in files
    assert "b.txt" in files
    assert ".env" in files  # kept
    assert "x.js" not in files
    assert "HEAD" not in files


def test_display_path_prefers_relative_to_root(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    f = tmp_path / "sub" / "file.py"
    f.write_text("x")
    s = cc_search._display_path(f, [tmp_path])
    # Accept either unix or windows slashes — pathlib can produce either.
    assert s in {"sub/file.py", "sub\\file.py"}


# --------------------------------------------------------------------------- #
#  5. global_search via SessionDB FTS5
# --------------------------------------------------------------------------- #


def test_fts5_escape_wraps_query_as_phrase() -> None:
    """Free-text queries must be quoted before hitting FTS5 or the
    virtual table will raise on punctuation (``:``, ``(``, etc.) and on
    reserved operator words."""
    assert cc_search._fts5_escape("hello world") == '"hello world"'
    # Embedded double-quotes get doubled (SQL-style escape).
    assert cc_search._fts5_escape('say "hi"') == '"say ""hi"""'
    # Empty / whitespace-only queries pass through untouched so callers
    # can short-circuit before calling SessionDB.search().
    assert cc_search._fts5_escape("   ") == ""


def test_global_search_backend_uses_fts5(tmp_path: Path) -> None:
    """The dialog's data source is ``SessionDB.search()``. Seed two
    sessions with distinct content, then verify the FTS5 query surfaces
    the right rows. Covers the (session_id, Message) wire format without
    spinning up the prompt_toolkit dialog.
    """
    db = SessionDB(db_path=tmp_path / "s.db")
    try:
        sid_a = db.create_session(model="m", provider="p", cwd=str(tmp_path))
        db.add_message(sid_a, Message(role="user", content="refactor the search dialog"))
        db.add_message(sid_a, Message(role="assistant", content="done"))

        sid_b = db.create_session(model="m", provider="p", cwd=str(tmp_path))
        db.add_message(sid_b, Message(role="user", content="unrelated payment feature"))

        rows = db.search("search")
        assert rows, "FTS5 should match the word 'search' in session A"
        assert rows[0]["session_id"] == sid_a
        assert "search" in rows[0]["content"].lower()

        # Negative — query with no hits returns an empty list.
        assert db.search("zzzquantum") == []

        # A multi-word phrase with punctuation would blow up raw FTS5;
        # the _fts5_escape helper turns it into a safe phrase match.
        escaped = cc_search._fts5_escape("search dialog")
        rows_phrase = db.search(escaped)
        assert rows_phrase and rows_phrase[0]["session_id"] == sid_a
    finally:
        db.close()


# --------------------------------------------------------------------------- #
#  6. history_search filtering (no dialog) — verifies user-only filter +
#     ranking via the internal _rank_strings / fuzzy helpers.
# --------------------------------------------------------------------------- #


def test_history_search_filters_user_messages_and_ranks() -> None:
    msgs = [
        Message(role="user", content="deploy the search feature"),
        Message(role="assistant", content="deployed!"),
        Message(role="user", content="write docs for deploy"),
        Message(role="tool", content="ignored tool output"),
        Message(role="user", content=""),  # empty — filtered out
    ]
    user_prompts = [
        m for m in msgs if m.role == "user" and (m.content or "").strip()
    ]
    assert len(user_prompts) == 2

    ranked_texts = cc_search.fuzzy_match(
        [m.content for m in user_prompts], "deploy"
    )
    # Both contain "deploy" — order is preserved for ties within the
    # exact-substring bucket (rapidfuzz may reorder by score).
    assert len(ranked_texts) == 2
    for t in ranked_texts:
        assert "deploy" in t.lower()
