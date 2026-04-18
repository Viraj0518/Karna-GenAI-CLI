"""Rich Style + Theme objects built from `design_tokens`.

This module is a *presentation layer* over `design_tokens`. All raw hex
values live there; here we lift them into Rich primitives for the rest
of the TUI to consume.

Backwards compatibility: legacy names (`BRAND_BLUE`, `USER_INPUT`,
`ASSISTANT_TEXT`, `KARNA_THEME`, …) are preserved so existing importers
in output.py, banner.py, repl.py continue to work.

New semantic names (preferred going forward):
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_TERTIARY, TEXT_DISABLED,
    ACCENT_BRAND, ACCENT_CYAN, ACCENT_SUCCESS, ACCENT_WARNING,
    ACCENT_DANGER, ACCENT_THINKING, THINKING, META, DIVIDER, …
"""

from __future__ import annotations

from rich.style import Style
from rich.theme import Theme

from karna.tui.design_tokens import COLORS, SEMANTIC, TYPOGRAPHY


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #

def _style(color: str, *, bold: bool = False, italic: bool = False,
           dim: bool = False) -> Style:
    """Build a Rich Style from a hex color + flags."""
    return Style(color=color or None, bold=bold, italic=italic, dim=dim)


def _typ(role: str) -> Style:
    """Lift a TypeStyle from design_tokens into a Rich Style."""
    t = TYPOGRAPHY[role]
    return _style(t.color, bold=t.bold, italic=t.italic, dim=t.dim)


# --------------------------------------------------------------------------- #
#  Brand / palette re-exports (hex strings)
# --------------------------------------------------------------------------- #

BRAND_BLUE      = COLORS.accent.brand      # kept for legacy imports
ACCENT_BRAND    = COLORS.accent.brand
ACCENT_HOVER    = COLORS.accent.hover
ACCENT_CYAN     = COLORS.accent.cyan
ACCENT_SUCCESS  = COLORS.accent.success
ACCENT_WARNING  = COLORS.accent.warning
ACCENT_DANGER   = COLORS.accent.danger
ACCENT_THINKING = COLORS.accent.thinking

TEXT_PRIMARY    = COLORS.text.primary
TEXT_SECONDARY  = COLORS.text.secondary
TEXT_TERTIARY   = COLORS.text.tertiary
TEXT_DISABLED   = COLORS.text.disabled

BG_SUBTLE       = COLORS.bg.subtle
BG_RAISED       = COLORS.bg.raised
BORDER_SUBTLE   = COLORS.border.subtle
BORDER_ACCENT   = COLORS.border.accent


# --------------------------------------------------------------------------- #
#  Rich Style objects — typography roles
# --------------------------------------------------------------------------- #

HEADING_1   = _typ("heading.1")
HEADING_2   = _typ("heading.2")
BODY        = _typ("body")
CAPTION     = _typ("caption")
MICRO       = _typ("micro")
CODE        = _typ("code")
EMPHASIS    = _typ("emphasis")
META        = _typ("meta")


# --------------------------------------------------------------------------- #
#  Rich Style objects — semantic / conversation roles
# --------------------------------------------------------------------------- #

USER            = _style(COLORS.text.primary)
ASSISTANT       = _style(COLORS.accent.cyan)
THINKING        = _style(COLORS.accent.thinking, italic=True, dim=True)

TOOL_NAME       = _style(COLORS.accent.brand, bold=True)
TOOL_ARGS       = _style(COLORS.text.secondary)
TOOL_PENDING    = _style(COLORS.text.tertiary, dim=True)
TOOL_RUNNING    = _style(COLORS.accent.warning)
TOOL_OK         = _style(COLORS.accent.success)
TOOL_ERR        = _style(COLORS.accent.danger, bold=True)

DIVIDER         = _style(COLORS.border.subtle, dim=True)
PANEL_BORDER    = _style(COLORS.border.accent)
PROMPT          = _style(COLORS.accent.cyan, bold=True)
SPINNER         = _style(COLORS.accent.brand)
ERROR           = _style(COLORS.accent.danger, bold=True)


# --------------------------------------------------------------------------- #
#  Legacy names (kept so existing code keeps working)
# --------------------------------------------------------------------------- #

USER_INPUT      = USER
ASSISTANT_TEXT  = ASSISTANT
TOOL_CALL       = _style(COLORS.accent.warning)        # yellow-ish
TOOL_RESULT     = _style(COLORS.accent.success, dim=True)
COST_INFO       = _style(COLORS.text.tertiary)
DIM             = Style(dim=True)

# Banner styles — match the existing visual so banner.py keeps looking right
BANNER_BORDER   = PANEL_BORDER
BANNER_TITLE    = _style(COLORS.accent.cyan, bold=True)
BANNER_KEY      = _style(COLORS.text.tertiary)
BANNER_VALUE    = _style(COLORS.text.primary)


# --------------------------------------------------------------------------- #
#  Rich Theme — for Console(theme=…)
# --------------------------------------------------------------------------- #

def _build_theme() -> Theme:
    """Assemble the Rich Theme from semantic tokens.

    Keys are the strings callers pass as [role]…[/role] markup or as the
    `style=` argument on Console.print. Keep legacy names populated.
    """
    styles: dict[str, str] = {
        # ── Conversation roles ──────────────────────────────────────
        "user":             SEMANTIC["role.user"],
        "assistant":        SEMANTIC["role.assistant"],
        "thinking":         f"italic dim {SEMANTIC['role.thinking']}",
        "system":           SEMANTIC["role.system"],

        # ── Tool-call lifecycle ─────────────────────────────────────
        "tool.name":        f"bold {SEMANTIC['tool.name']}",
        "tool.args":        SEMANTIC["tool.args"],
        "tool.status.pending": f"dim {SEMANTIC['tool.pending']}",
        "tool.status.running": SEMANTIC["tool.running"],
        "tool.status.ok":      SEMANTIC["tool.ok"],
        "tool.status.err":     f"bold {SEMANTIC['tool.err']}",

        # Legacy aliases
        "tool.call":        SEMANTIC["tool.running"],
        "tool.result":      f"dim {SEMANTIC['tool.ok']}",

        # ── Typography ──────────────────────────────────────────────
        "heading.1":        f"bold {SEMANTIC['text.primary']}",
        "heading.2":        f"bold {SEMANTIC['text.primary']}",
        "body":             SEMANTIC["text.primary"],
        "caption":          SEMANTIC["text.secondary"],
        "micro":            SEMANTIC["text.tertiary"],
        "emphasis":         f"italic {SEMANTIC['accent.brand']}",
        "meta":             f"dim {SEMANTIC['meta']}",

        # ── Chrome ──────────────────────────────────────────────────
        "divider":          f"dim {SEMANTIC['divider']}",
        "panel.border":     SEMANTIC["border.accent"],
        "border.subtle":    SEMANTIC["border.subtle"],
        "border.accent":    SEMANTIC["border.accent"],
        "prompt":           f"bold {SEMANTIC['prompt']}",
        "dim":              "dim",
        "error":            f"bold {SEMANTIC['accent.danger']}",
        "warning":          SEMANTIC["accent.warning"],
        "success":          SEMANTIC["accent.success"],
        "cost":             SEMANTIC["cost"],

        # ── Banner (legacy, preserved) ──────────────────────────────
        "banner.border":    SEMANTIC["border.accent"],
        "banner.title":     f"bold {SEMANTIC['accent.cyan']}",
        "banner.key":       SEMANTIC["text.tertiary"],
        "banner.value":     SEMANTIC["text.primary"],
    }
    return Theme(styles)


KARNA_THEME = _build_theme()


__all__ = [
    # Palette re-exports
    "BRAND_BLUE",
    "ACCENT_BRAND", "ACCENT_HOVER", "ACCENT_CYAN",
    "ACCENT_SUCCESS", "ACCENT_WARNING", "ACCENT_DANGER", "ACCENT_THINKING",
    "TEXT_PRIMARY", "TEXT_SECONDARY", "TEXT_TERTIARY", "TEXT_DISABLED",
    "BG_SUBTLE", "BG_RAISED", "BORDER_SUBTLE", "BORDER_ACCENT",
    # Typography
    "HEADING_1", "HEADING_2", "BODY", "CAPTION", "MICRO",
    "CODE", "EMPHASIS", "META",
    # Roles
    "USER", "ASSISTANT", "THINKING",
    "TOOL_NAME", "TOOL_ARGS", "TOOL_PENDING",
    "TOOL_RUNNING", "TOOL_OK", "TOOL_ERR",
    "DIVIDER", "PANEL_BORDER", "PROMPT", "SPINNER", "ERROR",
    # Legacy
    "USER_INPUT", "ASSISTANT_TEXT", "TOOL_CALL", "TOOL_RESULT",
    "COST_INFO", "DIM",
    "BANNER_BORDER", "BANNER_TITLE", "BANNER_KEY", "BANNER_VALUE",
    # Theme
    "KARNA_THEME",
]
