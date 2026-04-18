"""Default keybinding table for Nellie.

Keys are logical action names; values are prompt_toolkit-style key
descriptors (``"ctrl+c"``, ``"enter"``, ``"up"``, …). The six actions
below are the minimum the prompt needs; more can be added later without
breaking existing configs because :func:`load_bindings` always starts
from this dict and overlays the user file on top.
"""

from __future__ import annotations

from typing import Mapping

DEFAULT_BINDINGS: Mapping[str, str] = {
    "cancel": "ctrl+c",
    "submit": "enter",
    "newline": "ctrl+j",
    "history_up": "up",
    "history_down": "down",
    "toggle_vim": "ctrl+v",
}

__all__ = ["DEFAULT_BINDINGS"]
