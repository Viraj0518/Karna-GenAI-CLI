"""Design tokens for Nellie TUI — colors, typography, spacing, roles.

Source of truth. `themes.py` builds Rich Style/Theme objects from these.
Consumers (output.py, input.py, banner.py, slash.py, repl.py) should
prefer importing the semantic roles here (or the Rich styles derived in
themes.py) rather than raw hex codes.

Design intent: dark-first, quiet, generous whitespace, brand accent used
sparingly. Inspired by Claude Code's Ink TUI, Warp, and Charm bubbletea.

Skin system (ported from hermes-agent):
  Users can override colors in ``~/.karna/config.toml`` under ``[theme]``.
  The ``SkinConfig`` dataclass and ``load_skin()`` / ``get_active_skin()``
  helpers let the rest of the TUI resolve colors at runtime without
  hard-coding hex values.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

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


# --------------------------------------------------------------------------- #
#  Skin / theme engine (ported from hermes-agent skin_engine.py)
# --------------------------------------------------------------------------- #


@dataclass
class SkinConfig:
    """Complete skin configuration.

    Maps semantic color names to hex values. Users can override any of
    these in ``~/.karna/config.toml`` under ``[theme]``.
    """

    name: str = "default"
    description: str = "Karna blue (default)"

    # Color overrides -- keys match SEMANTIC role names.
    colors: dict[str, str] = field(default_factory=dict)

    # Spinner customisation (kawaii faces, verbs, wing decorators)
    spinner: dict[str, Any] = field(default_factory=dict)

    # Branding strings
    branding: dict[str, str] = field(default_factory=dict)

    # Tool output prefix character (e.g. "┊")
    tool_prefix: str = "\u250a"

    # Per-tool emoji overrides
    tool_emojis: dict[str, str] = field(default_factory=dict)

    def get_color(self, key: str, fallback: str = "") -> str:
        """Get a color value with fallback."""
        return self.colors.get(key, fallback)

    def get_branding(self, key: str, fallback: str = "") -> str:
        """Get a branding value with fallback."""
        return self.branding.get(key, fallback)

    def get_spinner_wings(self) -> list[tuple[str, str]]:
        """Get spinner wing pairs, or empty list if none defined."""
        raw = self.spinner.get("wings", [])
        result: list[tuple[str, str]] = []
        for pair in raw:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                result.append((str(pair[0]), str(pair[1])))
        return result


# Built-in Karna blue skin -- all values match the palette above.
_DEFAULT_SKIN_COLORS: dict[str, str] = {
    "accent.brand": COLORS.accent.brand,
    "accent.hover": COLORS.accent.hover,
    "accent.cyan": COLORS.accent.cyan,
    "accent.success": COLORS.accent.success,
    "accent.warning": COLORS.accent.warning,
    "accent.danger": COLORS.accent.danger,
    "accent.thinking": COLORS.accent.thinking,
    "text.primary": COLORS.text.primary,
    "text.secondary": COLORS.text.secondary,
    "text.tertiary": COLORS.text.tertiary,
    "text.disabled": COLORS.text.disabled,
    "bg.subtle": COLORS.bg.subtle,
    "bg.raised": COLORS.bg.raised,
    "border.subtle": COLORS.border.subtle,
    "border.accent": COLORS.border.accent,
}

# Active skin -- lazily initialised via ``get_active_skin()``.
_active_skin: SkinConfig | None = None


def _load_skin_from_config() -> SkinConfig:
    """Load skin overrides from ``~/.karna/config.toml`` ``[theme]`` section.

    Falls back to the built-in Karna blue skin on any error.
    """
    import sys

    config_path = Path.home() / ".karna" / "config.toml"
    if not config_path.exists():
        return SkinConfig(colors=dict(_DEFAULT_SKIN_COLORS))

    try:
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib  # type: ignore[no-redef]

        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception as exc:
        logger.debug("Could not read config for theme: %s", exc)
        return SkinConfig(colors=dict(_DEFAULT_SKIN_COLORS))

    theme_data = data.get("theme", {})
    if not isinstance(theme_data, dict):
        return SkinConfig(colors=dict(_DEFAULT_SKIN_COLORS))

    # Start from default colors, then overlay user overrides.
    colors = dict(_DEFAULT_SKIN_COLORS)
    user_colors = theme_data.get("colors", {})
    if isinstance(user_colors, dict):
        colors.update(user_colors)

    spinner = theme_data.get("spinner", {})
    if not isinstance(spinner, dict):
        spinner = {}

    branding = theme_data.get("branding", {})
    if not isinstance(branding, dict):
        branding = {}

    tool_emojis = theme_data.get("tool_emojis", {})
    if not isinstance(tool_emojis, dict):
        tool_emojis = {}

    return SkinConfig(
        name=theme_data.get("name", "custom") if user_colors else "default",
        description=theme_data.get("description", ""),
        colors=colors,
        spinner=spinner,
        branding=branding,
        tool_prefix=str(theme_data.get("tool_prefix", "\u250a")),
        tool_emojis=tool_emojis,
    )


def get_active_skin() -> SkinConfig:
    """Get the active skin config (cached on first call)."""
    global _active_skin
    if _active_skin is None:
        _active_skin = _load_skin_from_config()
    return _active_skin


def set_active_skin(skin: SkinConfig) -> None:
    """Set a new active skin (e.g. for live /skin switching)."""
    global _active_skin
    _active_skin = skin


def reset_skin_cache() -> None:
    """Clear the cached skin so the next ``get_active_skin()`` reloads from config."""
    global _active_skin
    _active_skin = None


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
    "SkinConfig",
    "get_active_skin",
    "set_active_skin",
    "reset_skin_cache",
]
