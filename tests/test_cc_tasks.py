"""Tests for the CC-ported task / compact / session / agent renderers.

Happy-path + edge cases for each of the six renderers in
`karna.tui.cc_components.tasks`. Pure module, no IO — straight unit
tests.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from karna.tui.cc_components import tasks as cc_tasks


def _render_plain(renderable) -> str:
    """Render a Rich object to plain text for assertions."""
    buf = Console(record=True, width=120, color_system=None, force_terminal=False)
    buf.print(renderable)
    return buf.export_text(clear=True)


# --------------------------------------------------------------------------- #
#  1. render_task_list — empty + all four statuses + owner/blocker
# --------------------------------------------------------------------------- #


def test_render_task_list_empty_and_all_statuses() -> None:
    # Empty list → placeholder, never raises.
    empty_out = _render_plain(cc_tasks.render_task_list([]))
    assert "No tasks yet" in empty_out
    assert cc_tasks.GLYPH_PENDING in empty_out

    tasks = [
        {"id": "1", "subject": "Draft proposal", "status": "pending"},
        {"id": "2", "subject": "Ship v1",         "status": "in_progress",
         "owner": "alpha"},
        {"id": "3", "subject": "Deploy",          "status": "completed"},
        {"id": "4", "subject": "Scrap idea",      "status": "deleted"},
        {"id": "5", "subject": "Blocked work",    "status": "pending",
         "blockedBy": ["2"]},
    ]
    out = _render_plain(cc_tasks.render_task_list(tasks))

    # Every status glyph surfaces exactly where the spec demands.
    assert cc_tasks.GLYPH_PENDING in out       # ○
    assert cc_tasks.GLYPH_IN_PROGRESS in out   # ◐
    assert cc_tasks.GLYPH_COMPLETED in out     # ●
    assert cc_tasks.GLYPH_DELETED in out       # ×
    # Subjects all present
    for subj in ("Draft proposal", "Ship v1", "Deploy", "Scrap idea",
                 "Blocked work"):
        assert subj in out
    # Owner tag present with @ prefix
    assert "@alpha" in out
    # Blocker subtitle rendered
    assert "blocked by #2" in out


def test_render_task_list_sort_priority_matches_cc() -> None:
    """CC prefers in_progress > pending > completed > deleted."""
    tasks = [
        {"id": "10", "subject": "completed-10", "status": "completed"},
        {"id": "20", "subject": "deleted-20",   "status": "deleted"},
        {"id": "30", "subject": "pending-30",   "status": "pending"},
        {"id": "40", "subject": "in_progress-40", "status": "in_progress"},
    ]
    out = _render_plain(cc_tasks.render_task_list(tasks))
    # Order in rendered output: in_progress first, deleted last.
    i_ip = out.find("in_progress-40")
    i_p = out.find("pending-30")
    i_c = out.find("completed-10")
    i_d = out.find("deleted-20")
    assert 0 <= i_ip < i_p < i_c < i_d, (
        f"sort order wrong: ip={i_ip}, p={i_p}, c={i_c}, d={i_d}"
    )


# --------------------------------------------------------------------------- #
#  2. render_compact_summary — token counts + message pluralisation
# --------------------------------------------------------------------------- #


def test_render_compact_summary_formats_tokens_and_counts() -> None:
    panel = cc_tasks.render_compact_summary(
        before_tokens=45_000,
        after_tokens=8_000,
        messages_removed=18,
        summary_text="Discussed SNN F1 regressions and planned V3 rollout.",
    )
    assert isinstance(panel, Panel)
    out = _render_plain(panel)
    assert "Auto-compact" in out
    assert "45k" in out
    assert "8k" in out
    assert cc_tasks.GLYPH_ARROW in out  # → glyph
    assert "18 messages summarised" in out
    assert "SNN F1 regressions" in out

    # Singular form for messages_removed == 1
    panel_one = cc_tasks.render_compact_summary(1_200, 400, 1, "")
    out_one = _render_plain(panel_one)
    assert "1 message summarised" in out_one
    # Empty summary falls back to the (no summary) placeholder.
    assert "(no summary)" in out_one


# --------------------------------------------------------------------------- #
#  3. render_resume_task_prompt
# --------------------------------------------------------------------------- #


def test_render_resume_task_prompt_includes_subject_and_choice() -> None:
    t = cc_tasks.render_resume_task_prompt({
        "id": "7",
        "subject": "Finish auto-compact wiring",
        "status": "in_progress",
    })
    assert isinstance(t, Text)
    out = _render_plain(t)
    assert "Resume" in out
    assert "Finish auto-compact wiring" in out
    assert "[y/N]" in out
    assert cc_tasks.GLYPH_IN_PROGRESS in out

    # Edge: missing subject → falls back to "last task" without raising.
    out_empty = _render_plain(cc_tasks.render_resume_task_prompt({}))
    assert "last task" in out_empty


# --------------------------------------------------------------------------- #
#  4. render_session_preview
# --------------------------------------------------------------------------- #


def test_render_session_preview_tail_and_empty() -> None:
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello, how can I help?"},
        {"role": "user", "content": "Port CC visuals."},
        {"role": "assistant", "content": "On it."},
        # Anthropic-style block list input
        {"role": "user", "content": [{"type": "text", "text": "thanks!"}]},
    ]
    renderable = cc_tasks.render_session_preview(
        "01HZX-demo", messages, max_messages=3,
    )
    out = _render_plain(renderable)
    assert "01HZX-demo" in out
    assert "5 messages" in out
    # Only the last 3 are previewed.
    assert "Port CC visuals." in out
    assert "On it." in out
    assert "thanks!" in out
    # Earlier messages trimmed out.
    assert "Hello, how can I help?" not in out

    # Empty messages → still renders a header + "(no messages yet)".
    empty = cc_tasks.render_session_preview("abc", [])
    out_empty = _render_plain(empty)
    assert "abc" in out_empty
    assert "0 messages" in out_empty
    assert "(no messages yet)" in out_empty


# --------------------------------------------------------------------------- #
#  5. render_session_background_hint
# --------------------------------------------------------------------------- #


def test_render_session_background_hint_zero_returns_none_and_positive_renders() -> None:
    assert cc_tasks.render_session_background_hint(0) is None
    assert cc_tasks.render_session_background_hint(-3) is None

    one = cc_tasks.render_session_background_hint(1)
    assert isinstance(one, Text)
    out_one = _render_plain(one)
    assert "1 session running in background" in out_one  # singular
    assert "ctrl+b" in out_one

    many = cc_tasks.render_session_background_hint(4)
    out_many = _render_plain(many)
    assert "4 sessions running in background" in out_many  # plural


# --------------------------------------------------------------------------- #
#  6. render_agent_list — empty + mixed statuses
# --------------------------------------------------------------------------- #


def test_render_agent_list_empty_and_mixed_statuses() -> None:
    empty_out = _render_plain(cc_tasks.render_agent_list([]))
    assert "No agents running" in empty_out

    agents = [
        {"name": "alpha", "status": "running", "currentTool": "Bash"},
        {"name": "beta",  "status": "done",    "currentTool": "Write"},
        {"name": "gamma", "status": "error",   "subtitle": "rate limited"},
        {"name": "delta", "status": "idle"},
    ]
    out = _render_plain(cc_tasks.render_agent_list(agents))

    for n in ("alpha", "beta", "gamma", "delta"):
        assert n in out
    # Tools rendered where supplied
    assert "Bash" in out
    assert "Write" in out
    # Subtitle surfaced
    assert "rate limited" in out
    # Each status family gets its designated glyph
    assert cc_tasks.GLYPH_IN_PROGRESS in out  # running
    assert cc_tasks.GLYPH_COMPLETED in out    # done
    assert cc_tasks.GLYPH_DELETED in out      # error
    assert cc_tasks.GLYPH_PENDING in out      # idle
