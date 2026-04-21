"""Design tokens for Nellie TUI — colors, typography, spacing, roles.

Source of truth. `themes.py` builds Rich Style/Theme objects from these.
Consumers (output.py, input.py, banner.py, slash.py, repl.py) should
prefer importing the semantic roles here (or the Rich styles derived in
themes.py) rather than raw hex codes.

Design intent: dark-first, quiet, generous whitespace, brand accent used
sparingly. Inspired by upstream reference's Ink TUI, Warp, and Charm bubbletea.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

# --------------------------------------------------------------------------- #
#  Palette — raw hex values (the only place these should be literal)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Background:
    """Surface colors. `canvas` is rendered via terminal reset (no explicit)."""

    canvas: str = ""  # pure black / terminal default
    subtle: str = "#0E0F12"  # cards, panels
    raised: str = "#1A1D23"  # active elements, hover, selection


@dataclass(frozen=True)
class Border:
    subtle: str = "#2A2F38"
    accent: str = "#3C73BD"  # brand


@dataclass(frozen=True)
class Text:
    primary: str = "#E6E8EC"
    secondary: str = "#A0A4AD"
    tertiary: str = "#5F6472"
    disabled: str = "#3A3D45"


@dataclass(frozen=True)
class Accent:
    brand: str = "#3C73BD"  # Karna blue
    hover: str = "#5A8FCC"
    cyan: str = "#87CEEB"  # assistant output
    success: str = "#7DCFA1"
    warning: str = "#E8C26B"
    danger: str = "#E87C7C"
    thinking: str = "#9F7AEA"  # reasoning / inner monologue


@dataclass(frozen=True)
class Palette:
    bg: Background = field(default_factory=Background)
    border: Border = field(default_factory=Border)
    text: Text = field(default_factory=Text)
    accent: Accent = field(default_factory=Accent)


COLORS = Palette()


# --------------------------------------------------------------------------- #
#  Semantic roles — what UI concept maps to which palette entry
#  These are what the rest of the TUI should reach for.
# --------------------------------------------------------------------------- #

SEMANTIC: Mapping[str, str] = {
    # Surfaces
    "bg.subtle": COLORS.bg.subtle,
    "bg.raised": COLORS.bg.raised,
    "border.subtle": COLORS.border.subtle,
    "border.accent": COLORS.border.accent,
    # Text
    "text.primary": COLORS.text.primary,
    "text.secondary": COLORS.text.secondary,
    "text.tertiary": COLORS.text.tertiary,
    "text.disabled": COLORS.text.disabled,
    # Accents
    "accent.brand": COLORS.accent.brand,
    "accent.hover": COLORS.accent.hover,
    "accent.cyan": COLORS.accent.cyan,
    "accent.success": COLORS.accent.success,
    "accent.warning": COLORS.accent.warning,
    "accent.danger": COLORS.accent.danger,
    "accent.thinking": COLORS.accent.thinking,
    # Conversation roles
    "role.user": COLORS.text.primary,
    "role.assistant": COLORS.accent.cyan,
    "role.thinking": COLORS.accent.thinking,
    "role.system": COLORS.text.secondary,
    # Tool-call lifecycle
    "tool.name": COLORS.accent.brand,
    "tool.args": COLORS.text.secondary,
    "tool.pending": COLORS.text.tertiary,
    "tool.running": COLORS.accent.warning,
    "tool.ok": COLORS.accent.success,
    "tool.err": COLORS.accent.danger,
    # Chrome
    "meta": COLORS.text.secondary,  # WCAG AA: 10:1 on bg.subtle (was tertiary 3.3:1)
    "divider": COLORS.border.subtle,
    "prompt": COLORS.accent.cyan,
    "cost": COLORS.text.secondary,  # WCAG AA: 10:1 (was tertiary 3.3:1)
}


# --------------------------------------------------------------------------- #
#  Spacing — integer column counts (Rich uses chars, not pixels)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Spacing:
    xs: int = 1
    sm: int = 2
    md: int = 4
    lg: int = 6
    xl: int = 8


SPACING = Spacing()


# --------------------------------------------------------------------------- #
#  Typography — Rich controls weight/italic/dim, not font family.
#  Each role is a tuple of (color_hex, bold, italic, dim) — themes.py
#  lifts these into Rich Style objects.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TypeStyle:
    color: str
    bold: bool = False
    italic: bool = False
    dim: bool = False


TYPOGRAPHY: Mapping[str, TypeStyle] = {
    "heading.1": TypeStyle(color=COLORS.text.primary, bold=True),
    "heading.2": TypeStyle(color=COLORS.text.primary, bold=True),
    "body": TypeStyle(color=COLORS.text.primary),
    "caption": TypeStyle(color=COLORS.text.secondary),
    "micro": TypeStyle(color=COLORS.text.tertiary),
    "code": TypeStyle(color=COLORS.text.primary),  # Rich.Syntax owns highlighting
    "emphasis": TypeStyle(color=COLORS.accent.brand, italic=True),
    "meta": TypeStyle(color=COLORS.text.secondary, dim=True),
}


__all__ = [
    "COLORS",
    "SEMANTIC",
    "SPACING",
    "TYPOGRAPHY",
    "Palette",
    "Background",
    "Border",
    "Text",
    "Accent",
    "Spacing",
    "TypeStyle",
]
