"""Tests for the CC-ported status-line + context indicators.

One test per exposed function, plus threshold-color sanity checks. The
module is pure (no IO, no polling) so these are straight unit tests.
"""

from __future__ import annotations

import pytest
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from karna.tui.cc_components import status as cc_status


def _render_plain(renderable) -> str:
    """Render a Rich object to plain text for assertions."""
    buf = Console(record=True, width=120, color_system=None, force_terminal=False)
    buf.print(renderable)
    return buf.export_text(clear=True)


# --------------------------------------------------------------------------- #
#  1. render_status_line
# --------------------------------------------------------------------------- #


def test_render_status_line_contains_all_segments_and_brand_ansi() -> None:
    out = cc_status.render_status_line(
        model="Opus 4.7",
        session_time="12m34s",
        tokens_used=12_300,
        context_window=200_000,
        cost_usd=0.42,
        agent_running=True,
        queued=2,
    )
    # Brand hex #3C73BD → 60;115;189 in truecolor ANSI
    assert "38;2;60;115;189" in out, "brand color #3C73BD missing from status line"
    assert "Opus 4.7" in out
    assert "12m34s" in out
    # 12300/200000 ≈ 6 %, tokens formatted as "12.3k"
    assert "12.3k" in out
    assert "200k" in out
    assert "$0.42" in out
    assert "running" in out
    assert "2 queued" in out
    # Idle branch when agent is off
    idle = cc_status.render_status_line(
        model="Opus 4.7",
        session_time="0s",
        tokens_used=0,
        context_window=200_000,
        cost_usd=0.0,
        agent_running=False,
    )
    assert "idle" in idle


# --------------------------------------------------------------------------- #
#  2. render_context_bar  +  threshold colors
# --------------------------------------------------------------------------- #


def test_render_context_bar_renders_and_shows_percentage() -> None:
    bar = cc_status.render_context_bar(50_000, 200_000)
    assert isinstance(bar, Text)
    plain = _render_plain(bar)
    assert "50k" in plain
    assert "200k" in plain
    assert "(25%)" in plain  # 50k / 200k = 25 %


def test_context_color_thresholds_match_cc() -> None:
    """Green < 50 %, brand 50-80 %, warning 80-95 %, danger >= 95 %."""
    assert cc_status._context_color(10) == cc_status.SUCCESS
    assert cc_status._context_color(49.9) == cc_status.SUCCESS
    assert cc_status._context_color(50) == cc_status.BRAND
    assert cc_status._context_color(79.9) == cc_status.BRAND
    assert cc_status._context_color(80) == cc_status.WARNING
    assert cc_status._context_color(94.9) == cc_status.WARNING
    assert cc_status._context_color(95) == cc_status.DANGER
    assert cc_status._context_color(100) == cc_status.DANGER
    # And the constants line up with CC's TokenWarning.tsx
    assert cc_status.CTX_WARNING_THRESHOLD == 80
    assert cc_status.CTX_ERROR_THRESHOLD == 95


# --------------------------------------------------------------------------- #
#  3. render_token_warning  — returns None below threshold, Panel above
# --------------------------------------------------------------------------- #


def test_render_token_warning_silent_and_loud() -> None:
    # Below 80 % → no panel (CC's TokenWarning returns null)
    assert cc_status.render_token_warning(10_000, 200_000) is None
    assert cc_status.render_token_warning(159_000, 200_000) is None  # 79.5 %
    # 85 % → warning panel
    warn = cc_status.render_token_warning(170_000, 200_000)
    assert isinstance(warn, Panel)
    text = _render_plain(warn)
    assert "Context low" in text
    assert "15% remaining" in text
    # 97 % → error panel, different copy
    err = cc_status.render_token_warning(194_000, 200_000)
    assert isinstance(err, Panel)
    err_text = _render_plain(err)
    assert "Context low" in err_text
    assert "Run /compact now" in err_text


# --------------------------------------------------------------------------- #
#  4. render_effort_indicator
# --------------------------------------------------------------------------- #


def test_render_effort_indicator_levels_and_glyphs() -> None:
    off = _render_plain(cc_status.render_effort_indicator(False, None))
    assert "thinking: off" in off

    low = _render_plain(cc_status.render_effort_indicator(True, 1024))
    assert cc_status.GLYPH_EFFORT_LOW in low
    assert "low" in low

    med = _render_plain(cc_status.render_effort_indicator(True, 4096))
    assert cc_status.GLYPH_EFFORT_MEDIUM in med
    assert "medium" in med

    high = _render_plain(cc_status.render_effort_indicator(True, 16_000))
    assert cc_status.GLYPH_EFFORT_HIGH in high
    assert "high" in high

    mx = _render_plain(cc_status.render_effort_indicator(True, 32_000))
    assert cc_status.GLYPH_EFFORT_MAX in mx
    assert "max" in mx


# --------------------------------------------------------------------------- #
#  5. render_pr_badge
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "state,expected_color",
    [
        ("approved", cc_status.SUCCESS),
        ("changes_requested", cc_status.DANGER),
        ("pending", cc_status.WARNING),
        ("merged", cc_status.BRAND),
        ("", cc_status.SUBTLE),  # unknown → dim
    ],
)
def test_render_pr_badge_status_colors(state: str, expected_color: str) -> None:
    badge = cc_status.render_pr_badge(123, state)
    assert isinstance(badge, Text)
    plain = _render_plain(badge)
    assert "#123" in plain
    assert "PR" in plain
    # The selected color must show up in the style spans of the Text object.
    all_styles = " ".join(str(span.style) for span in badge.spans)
    assert expected_color.lower() in all_styles.lower()


# --------------------------------------------------------------------------- #
#  6. render_cost_threshold_alert
# --------------------------------------------------------------------------- #


def test_render_cost_threshold_alert_panel() -> None:
    alert = cc_status.render_cost_threshold_alert(5.23, 5.00)
    assert isinstance(alert, Panel)
    plain = _render_plain(alert)
    assert "$5.23" in plain
    assert "$5.00" in plain
    assert "cost threshold" in plain.lower()
    # Brand link to cost docs
    assert "docs.anthropic.com" in plain


# --------------------------------------------------------------------------- #
#  7. render_memory_usage
# --------------------------------------------------------------------------- #


def test_render_memory_usage_levels() -> None:
    # With a limit: ratio-based thresholds
    normal = _render_plain(cc_status.render_memory_usage(100 * 1024 * 1024, 1024 * 1024 * 1024))
    assert "mem " in normal
    assert "High memory" not in normal  # quiet under 75 %

    high = _render_plain(cc_status.render_memory_usage(800 * 1024 * 1024, 1024 * 1024 * 1024))
    assert "High memory (high)" in high
    assert "/heapdump" in high

    crit = _render_plain(cc_status.render_memory_usage(980 * 1024 * 1024, 1024 * 1024 * 1024))
    assert "High memory (critical)" in crit

    # Without a limit: absolute thresholds
    abs_high = _render_plain(cc_status.render_memory_usage(600 * 1024 * 1024, None))
    assert "High memory (high)" in abs_high
    abs_crit = _render_plain(cc_status.render_memory_usage(2 * 1024 * 1024 * 1024, None))
    assert "High memory (critical)" in abs_crit
