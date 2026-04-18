"""Icon glyphs with Nerd Font primary + ASCII fallback.

Nellie uses Nerd Font glyphs (https://www.nerdfonts.com/) when the terminal
supports them, and falls back to plain ASCII otherwise. Detection is lazy
and cached — the first access probes the environment and the result sticks
for the rest of the process.

Usage:
    from karna.tui.icons import icons
    print(f"{icons.tool_bash} running bash")
    print(f"{icons.success} done")

You can also force a mode (useful for tests, screenshots, or users who
dislike glyphs):
    from karna.tui.icons import IconSet
    ascii_icons = IconSet(force_ascii=True)
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict


# --------------------------------------------------------------------------- #
#  Capability detection
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def _detect_nerd_font() -> bool:
    """Heuristic: do we believe the terminal can render Nerd Font glyphs?

    Truly verifying glyph support requires round-tripping cursor-position
    queries, which isn't safe in all contexts (tests, pipes, CI). We use a
    layered heuristic instead.

    Returns True when:
      - stdout is not a TTY  -> False (no fancy output for pipes)
      - env var KARNA_NERD_FONT=1  -> True (explicit opt-in)
      - env var KARNA_NERD_FONT=0  -> False (explicit opt-out)
      - env var KARNA_ASCII=1      -> False (global ascii mode)
      - TERM_PROGRAM in a known-good set (WezTerm, Alacritty, iTerm.app,
        kitty, WarpTerminal, vscode, ghostty)  -> True
      - otherwise False (conservative default)
    """
    # Hard opt-outs first
    if os.environ.get("KARNA_ASCII") == "1":
        return False
    if os.environ.get("KARNA_NERD_FONT") == "0":
        return False

    # Hard opt-in
    if os.environ.get("KARNA_NERD_FONT") == "1":
        return True

    # No TTY = no glyph rendering guarantees
    if not sys.stdout.isatty():
        return False

    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    known_good = {
        "wezterm",
        "alacritty",
        "iterm.app",
        "kitty",
        "warpterminal",
        "vscode",
        "ghostty",
    }
    if term_program in known_good:
        return True

    # Some terminals set TERM but not TERM_PROGRAM
    term = os.environ.get("TERM", "").lower()
    if "kitty" in term or "alacritty" in term:
        return True

    return False


# --------------------------------------------------------------------------- #
#  Icon set
# --------------------------------------------------------------------------- #

# (nerd_font_glyph, ascii_fallback)
_GLYPHS: Dict[str, tuple[str, str]] = {
    # Tools
    "tool_bash":    ("\uf489",  "$"),    # terminal
    "tool_read":    ("\uf15c",  "R"),    # file-lines
    "tool_write":   ("\uf040",  "W"),    # pencil
    "tool_edit":    ("\uf044",  "E"),    # edit
    "tool_grep":    ("\uf002",  "/"),    # search
    "tool_glob":    ("\uf07b",  "*"),    # folder
    "tool_git":     ("\uf1d3",  "g"),    # git logo
    "tool_web":     ("\uf0ac",  "@"),    # globe
    "tool_mcp":     ("\uf1e6",  "~"),    # plug
    "tool_task":    ("\uf0ae",  "T"),    # list-check
    "tool_monitor": ("\uf200",  "M"),    # chart

    # Roles
    "user":         ("\uf007",  ">"),    # person
    "assistant":    ("\uf544",  "*"),    # robot / star
    "thinking":     ("\uf0eb",  "."),    # lightbulb
    "sparkle":      ("\uf890",  "*"),    # sparkle

    # Status
    "success":      ("\uf00c",  "ok"),   # check
    "error":        ("\uf00d",  "!!"),   # x
    "warning":      ("\uf071",  "!"),    # triangle
    "pending":      ("\uf251",  "..."),  # hourglass
    "running":      ("\uf46a",  ">"),    # play

    # Affordances
    "chevron_right":("\uf054",  ">"),
    "chevron_down": ("\uf078",  "v"),
    "bullet":       ("\u2022",  "-"),    # actual unicode bullet, widely supported
    "arrow_right":  ("\uf061",  "->"),
    "ellipsis":     ("\u2026",  "..."),
}


@dataclass
class IconSet:
    """Resolved icon glyphs for the current terminal.

    Access icons as attributes: `icons.tool_bash`, `icons.success`, etc.
    The resolution happens once at construction; pass `force_ascii=True`
    to bypass detection.
    """

    force_ascii: bool = False
    _use_nerd: bool = False

    def __post_init__(self) -> None:
        self._use_nerd = (not self.force_ascii) and _detect_nerd_font()

    def __getattr__(self, name: str) -> str:
        # __getattr__ only fires for names not found normally.
        # Guard against recursion during dataclass init.
        if name.startswith("_") or name in ("force_ascii",):
            raise AttributeError(name)
        pair = _GLYPHS.get(name)
        if pair is None:
            raise AttributeError(f"unknown icon: {name!r}")
        return pair[0] if self._use_nerd else pair[1]

    def get(self, name: str, default: str = "") -> str:
        """Dict-like accessor that won't raise."""
        pair = _GLYPHS.get(name)
        if pair is None:
            return default
        return pair[0] if self._use_nerd else pair[1]

    @property
    def uses_nerd_font(self) -> bool:
        return self._use_nerd

    @property
    def names(self) -> list[str]:
        return list(_GLYPHS.keys())


# Module-level default — lazily initialized the first time it's accessed.
# Tests that need deterministic output should construct their own IconSet.
icons = IconSet()


# Convenience: looked-up by dotted path for symmetry with design_tokens
# (so callers that want `icon.tool.bash` semantically can do it via dict).
ICONS: Dict[str, str] = {
    f"tool.{k[5:]}" if k.startswith("tool_") else k.replace("_", "."): (
        _GLYPHS[k][0] if icons.uses_nerd_font else _GLYPHS[k][1]
    )
    for k in _GLYPHS
}


__all__ = ["IconSet", "icons", "ICONS"]
