"""Color theme for the Nellie TUI.

Dark-background aesthetic matching the Karna brand palette.
All colors are defined here so the rest of the TUI can stay
presentation-agnostic.
"""

from __future__ import annotations

from rich.style import Style
from rich.theme import Theme

# ── Karna brand accent ──────────────────────────────────────────────────
BRAND_BLUE = "#3C73BD"

# ── Semantic palette ────────────────────────────────────────────────────
USER_INPUT = Style(color="white")
ASSISTANT_TEXT = Style(color="#87CEEB")  # light sky-blue
TOOL_CALL = Style(color="yellow")
TOOL_RESULT = Style(color="green", dim=True)
ERROR = Style(color="red", bold=True)
COST_INFO = Style(color="bright_black")  # dim gray
PANEL_BORDER = Style(color=BRAND_BLUE)
SPINNER = Style(color=BRAND_BLUE)
PROMPT = Style(color="#87CEEB", bold=True)
DIM = Style(dim=True)
BANNER_BORDER = Style(color=BRAND_BLUE)
BANNER_TITLE = Style(color="#87CEEB", bold=True)
BANNER_KEY = Style(color="bright_black")
BANNER_VALUE = Style(color="white")

# ── Rich Theme object (for Console(theme=…)) ───────────────────────────
KARNA_THEME = Theme(
    {
        "user": "white",
        "assistant": "#87CEEB",
        "tool.call": "yellow",
        "tool.result": "dim green",
        "error": "bold red",
        "cost": "bright_black",
        "panel.border": BRAND_BLUE,
        "prompt": "bold #87CEEB",
        "dim": "dim",
        "banner.border": BRAND_BLUE,
        "banner.title": "bold #87CEEB",
        "banner.key": "bright_black",
        "banner.value": "white",
    }
)
