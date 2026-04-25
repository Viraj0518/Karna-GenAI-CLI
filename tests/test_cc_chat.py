"""Tests for ``karna.tui.cc_components.chat`` — the upstream-ported chat renderers.

Covers role dispatch, per-renderer output snapshots, timestamp formatting,
interrupt handling, the actions-menu shape, the message-selector cursor,
and VirtualMessageList's windowed paging.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from karna.tui.cc_components.chat import (
    BRAND,
    DEFAULT_ACTIONS,
    INTERRUPT_MESSAGE,
    MAX_VISIBLE_MESSAGES,
    ChatMessage,
    format_timestamp,
    render_actions_menu,
    render_assistant_message,
    render_interrupted_by_user,
    render_message,
    render_message_selector,
    render_messages,
    render_system_message,
    render_timestamp,
    render_tool_message,
    render_user_message,
    wrap_response,
)
from karna.tui.hermes_display import (
    NELLIE_ASSISTANT_LABEL,
    NELLIE_TOOL_RESULT_GLYPH,
)


def _render(renderable, width: int = 100) -> str:
    """Render to plain text (no ANSI) for snapshot assertions."""
    console = Console(
        file=io.StringIO(),
        width=width,
        force_terminal=False,
        color_system=None,
        legacy_windows=False,
        record=True,
    )
    console.print(renderable)
    return console.export_text()


# -------------------------------------------------------------------- #
# Role dispatch
# -------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "role, expected_renderer",
    [
        ("user", render_user_message),
        ("assistant", render_assistant_message),
        ("tool", render_tool_message),
        ("system", render_system_message),
    ],
)
def test_render_message_dispatches_by_role(role, expected_renderer):
    msg = ChatMessage(role=role, content="hello")
    out = _render(render_message(msg))
    direct = _render(expected_renderer(msg))
    assert out == direct


def test_render_message_unknown_role_falls_back():
    msg = ChatMessage(role="nonsense", content="oops")
    out = _render(render_message(msg))
    assert "unknown role" in out
    assert "oops" in out


# -------------------------------------------------------------------- #
# Per-renderer snapshots — assistant label, user glyph, tool marker
# -------------------------------------------------------------------- #


def test_assistant_message_uses_nellie_label():
    msg = ChatMessage(role="assistant", content="Hi Viraj.")
    out = _render(render_assistant_message(msg))
    assert NELLIE_ASSISTANT_LABEL in out  # "◆ nellie"
    assert "Hi Viraj." in out


def test_user_message_has_prompt_glyph():
    msg = ChatMessage(role="user", content="what's the weather?")
    out = _render(render_user_message(msg))
    assert out.lstrip().startswith("\u276f")  # ❯
    assert "what's the weather?" in out


def test_tool_message_uses_continuation_marker():
    msg = ChatMessage(role="tool", content="42", tool_name="calculator", is_error=False)
    out = _render(render_tool_message(msg))
    # ⎿ marker lives in the response wrapper
    assert NELLIE_TOOL_RESULT_GLYPH in out
    assert "calculator" in out
    assert "42" in out


def test_tool_error_preserves_tool_name():
    err = ChatMessage(
        role="tool",
        content="file not found",
        tool_name="read_file",
        is_error=True,
    )
    out = _render(render_tool_message(err))
    assert "read_file" in out
    assert "file not found" in out


def test_system_message_snapshot():
    msg = ChatMessage(role="system", content="context compacted")
    out = _render(render_system_message(msg))
    # System prefix is the assistant-dot glyph ●
    assert "\u25cf" in out
    assert "context compacted" in out


# -------------------------------------------------------------------- #
# Interrupt handling
# -------------------------------------------------------------------- #


def test_interrupt_message_triggers_interrupted_line():
    msg = ChatMessage(role="user", content=INTERRUPT_MESSAGE)
    out = _render(render_user_message(msg))
    assert "Interrupted" in out
    assert "What should Nellie do instead?" in out


def test_render_interrupted_by_user_text_shape():
    t = render_interrupted_by_user()
    assert isinstance(t, Text)
    assert "Interrupted" in t.plain
    assert "Nellie" in t.plain


# -------------------------------------------------------------------- #
# Timestamp + model-label helpers
# -------------------------------------------------------------------- #


def test_format_timestamp_strips_leading_zero():
    # 01:05 PM → "1:05 PM" to match en-US locale
    assert format_timestamp("2026-04-20T13:05:00+00:00") == "1:05 PM"


def test_format_timestamp_none_for_empty():
    assert format_timestamp(None) is None
    assert format_timestamp("") is None
    assert format_timestamp("not-a-date") is None


def test_render_timestamp_empty_on_none():
    t = render_timestamp(None)
    assert isinstance(t, Text)
    assert t.plain == ""


# -------------------------------------------------------------------- #
# wrap_response nested-suppression
# -------------------------------------------------------------------- #


def test_wrap_response_adds_marker_once():
    body = Text("result body")
    out = _render(wrap_response(body))
    assert NELLIE_TOOL_RESULT_GLYPH in out


def test_wrap_response_nested_skips_marker():
    body = Text("result body")
    out = _render(wrap_response(body, nested=True))
    assert NELLIE_TOOL_RESULT_GLYPH not in out
    assert "result body" in out


# -------------------------------------------------------------------- #
# Actions menu shape
# -------------------------------------------------------------------- #


def test_actions_menu_returns_panel_with_default_keys():
    panel = render_actions_menu()
    assert isinstance(panel, Panel)
    out = _render(panel)
    # Every default action key should appear in the rendered panel
    for action in DEFAULT_ACTIONS:
        assert f"[{action.key}]" in out
        assert action.label in out


def test_actions_menu_brand_color_in_border():
    panel = render_actions_menu()
    # border_style is set to the brand hex — covers both literal + lower/upper
    assert BRAND.lower() in str(panel.border_style).lower()


# -------------------------------------------------------------------- #
# MessageSelector windowing
# -------------------------------------------------------------------- #


def test_message_selector_centers_selection_and_truncates_preview():
    msgs = [ChatMessage(role="user", content=f"prompt number {i}") for i in range(12)]
    panel = render_message_selector(msgs, selected_index=6)
    out = _render(panel)
    # With MAX_VISIBLE_MESSAGES=7 and selection 6, window covers indices 3..9.
    # The centered selection must appear with the ▸ cursor marker.
    assert "\u25b8" in out
    assert "prompt number 6" in out
    # Messages way before the window should NOT be visible.
    assert "prompt number 0" not in out
    assert "prompt number 11" not in out


def test_message_selector_handles_empty_input():
    panel = render_message_selector([], selected_index=0)
    out = _render(panel)
    assert "no messages" in out


def test_message_selector_max_visible_constant():
    assert MAX_VISIBLE_MESSAGES == 7  # matches upstream's MessageSelector


# -------------------------------------------------------------------- #
# VirtualMessageList pager
# -------------------------------------------------------------------- #


def test_render_messages_overflow_summary_when_over_max_visible():
    msgs = [ChatMessage(role="user", content=f"m{i}") for i in range(15)]
    out = _render(render_messages(msgs, max_visible=10))
    # 5 older items are paged off; summary rule appears, recent 10 render
    assert "5 older messages" in out
    assert "m14" in out
    assert "m5" in out
    assert "m0" not in out


def test_render_messages_no_overflow_rule_under_limit():
    msgs = [ChatMessage(role="user", content="only one")]
    out = _render(render_messages(msgs, max_visible=10))
    assert "older message" not in out
    assert "only one" in out


def test_render_messages_transcript_mode_includes_timestamp():
    msgs = [
        ChatMessage(
            role="assistant",
            content="hi",
            timestamp="2026-04-20T13:05:00+00:00",
            model="claude-opus-4-7",
        )
    ]
    out = _render(render_messages(msgs, is_transcript_mode=True))
    assert "1:05 PM" in out
    assert "claude-opus-4-7" in out
    assert "hi" in out
