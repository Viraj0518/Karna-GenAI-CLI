"""Tests for the Nellie TUI design system.

Covers three layers:
  1. design_tokens — palette, semantic roles, spacing, typography
  2. icons        — Nerd Font detection + ASCII fallback
  3. themes       — legacy imports, new semantic styles, Rich Theme
"""

from __future__ import annotations

import re

import pytest
from rich.style import Style
from rich.theme import Theme

from karna.tui import design_tokens as dt
from karna.tui import icons as icons_mod
from karna.tui import themes

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


# --------------------------------------------------------------------------- #
#  Palette
# --------------------------------------------------------------------------- #


def test_palette_values_are_valid_hex() -> None:
    """Every non-empty palette color should be a well-formed hex triplet."""
    all_colors = [
        dt.COLORS.bg.subtle,
        dt.COLORS.bg.raised,
        dt.COLORS.border.subtle,
        dt.COLORS.border.accent,
        dt.COLORS.text.primary,
        dt.COLORS.text.secondary,
        dt.COLORS.text.tertiary,
        dt.COLORS.text.disabled,
        dt.COLORS.accent.brand,
        dt.COLORS.accent.hover,
        dt.COLORS.accent.cyan,
        dt.COLORS.accent.success,
        dt.COLORS.accent.warning,
        dt.COLORS.accent.danger,
        dt.COLORS.accent.thinking,
    ]
    for c in all_colors:
        assert HEX.match(c), f"{c!r} is not a valid hex color"


def test_brand_matches_spec() -> None:
    assert dt.COLORS.accent.brand == "#3C73BD"
    assert dt.COLORS.accent.cyan == "#87CEEB"
    assert dt.COLORS.accent.thinking == "#9F7AEA"


def test_semantic_roles_present() -> None:
    required = {
        "text.primary",
        "text.secondary",
        "text.tertiary",
        "text.disabled",
        "accent.brand",
        "accent.cyan",
        "accent.success",
        "accent.warning",
        "accent.danger",
        "accent.thinking",
        "role.user",
        "role.assistant",
        "role.thinking",
        "tool.name",
        "tool.args",
        "tool.pending",
        "tool.running",
        "tool.ok",
        "tool.err",
        "meta",
        "divider",
        "prompt",
    }
    missing = required - set(dt.SEMANTIC.keys())
    assert not missing, f"missing semantic roles: {missing}"


def test_palette_is_frozen() -> None:
    """Tokens must be immutable — catches accidental runtime mutation."""
    with pytest.raises(Exception):  # FrozenInstanceError subclasses AttributeError/TypeError
        dt.COLORS.accent.brand = "#000000"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
#  Spacing & typography
# --------------------------------------------------------------------------- #


def test_spacing_is_integer_column_counts() -> None:
    assert dt.SPACING.xs == 1
    assert dt.SPACING.sm == 2
    assert dt.SPACING.md == 4
    assert dt.SPACING.lg == 6
    assert all(isinstance(v, int) for v in (dt.SPACING.xs, dt.SPACING.sm, dt.SPACING.md, dt.SPACING.lg, dt.SPACING.xl))


def test_typography_roles_complete() -> None:
    for role in ("heading.1", "heading.2", "body", "caption", "micro", "code", "emphasis", "meta"):
        assert role in dt.TYPOGRAPHY
        assert HEX.match(dt.TYPOGRAPHY[role].color)


# --------------------------------------------------------------------------- #
#  Icons
# --------------------------------------------------------------------------- #


def test_ascii_fallback_resolves_every_glyph() -> None:
    ascii_set = icons_mod.IconSet(force_ascii=True)
    for name in ascii_set.names:
        value = getattr(ascii_set, name)
        assert value, f"icon {name!r} has empty ASCII fallback"
        # Fallback must not be in the Nerd Font private-use area
        # (0xE000-0xF8FF) — that range only renders with a Nerd Font.
        # Basic Unicode (◆ ✓ ▸ etc) is OK on modern terminals.
        for ch in value:
            assert not (0xE000 <= ord(ch) <= 0xF8FF), (
                f"icon {name!r} fallback contains PUA (Nerd Font) glyph: {value!r}"
            )


def test_nerd_font_glyphs_in_private_use_area() -> None:
    """Sanity check: forced nerd-font icons return PUA glyphs."""
    nerd_set = icons_mod.IconSet(force_ascii=False)
    nerd_set._use_nerd = True  # force for deterministic test
    assert nerd_set.tool_bash == "\uf489"
    assert nerd_set.success == "\uf00c"


def test_required_icons_exist() -> None:
    ascii_set = icons_mod.IconSet(force_ascii=True)
    required = [
        "tool_bash",
        "tool_read",
        "tool_write",
        "tool_edit",
        "tool_grep",
        "tool_glob",
        "tool_git",
        "tool_web",
        "tool_mcp",
        "tool_task",
        "tool_monitor",
        "user",
        "assistant",
        "thinking",
        "sparkle",
        "success",
        "error",
        "pending",
        "chevron_right",
    ]
    for name in required:
        assert getattr(ascii_set, name), f"missing icon {name!r}"


def test_unknown_icon_raises() -> None:
    ascii_set = icons_mod.IconSet(force_ascii=True)
    with pytest.raises(AttributeError):
        _ = ascii_set.nonexistent_icon_xyz


def test_icon_get_returns_default_for_missing() -> None:
    ascii_set = icons_mod.IconSet(force_ascii=True)
    assert ascii_set.get("definitely_missing", default="?") == "?"


# --------------------------------------------------------------------------- #
#  Themes
# --------------------------------------------------------------------------- #


def test_legacy_imports_still_work() -> None:
    """Existing code imports these names — they must keep resolving."""
    assert themes.BRAND_BLUE == "#3C73BD"
    assert isinstance(themes.USER_INPUT, Style)
    assert isinstance(themes.ASSISTANT_TEXT, Style)
    assert isinstance(themes.TOOL_CALL, Style)
    assert isinstance(themes.TOOL_RESULT, Style)
    assert isinstance(themes.ERROR, Style)
    assert isinstance(themes.COST_INFO, Style)
    assert isinstance(themes.PANEL_BORDER, Style)
    assert isinstance(themes.SPINNER, Style)
    assert isinstance(themes.PROMPT, Style)
    assert isinstance(themes.BANNER_BORDER, Style)


def test_new_semantic_styles_exist() -> None:
    for attr in (
        "TEXT_PRIMARY",
        "TEXT_SECONDARY",
        "ACCENT_CYAN",
        "THINKING",
        "TOOL_NAME",
        "TOOL_OK",
        "TOOL_ERR",
        "DIVIDER",
        "META",
        "HEADING_1",
        "EMPHASIS",
    ):
        assert hasattr(themes, attr), f"themes missing {attr}"


def test_karna_theme_is_rich_theme_with_all_roles() -> None:
    assert isinstance(themes.KARNA_THEME, Theme)
    required_styles = {
        "user",
        "assistant",
        "thinking",
        "tool.name",
        "tool.args",
        "tool.status.pending",
        "tool.status.running",
        "tool.status.ok",
        "tool.status.err",
        "meta",
        "divider",
        # legacy aliases must still be present
        "panel.border",
        "banner.border",
        "prompt",
    }
    missing = required_styles - set(themes.KARNA_THEME.styles.keys())
    assert not missing, f"KARNA_THEME missing: {missing}"


def test_thinking_style_is_dim_italic() -> None:
    """The thinking role should feel quiet — italic + dim."""
    thinking = themes.THINKING
    assert thinking.italic is True
    assert thinking.dim is True
