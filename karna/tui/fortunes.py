"""Startup fortunes and daily tips for the Nellie TUI.

One fortune is shown after the banner on each REPL launch, printed in
dim text with a crystal-ball prefix.  A small pool of "legendary" drops
have a 5 %% chance of appearing instead of the regular rotation.
"""

from __future__ import annotations

import random

FORTUNES = [
    "you are one clean refactor away from clarity",
    "a tiny rename today prevents a huge bug tomorrow",
    "minimal diff, maximal calm",
    "today favors bold deletions over new abstractions",
    "tests are about to save your future self",
    "your instincts are correctly suspicious of that one branch",
    "the best comment explains *why*, never *what*",
    "ship small, learn fast",
    "a well-named variable is worth a paragraph of docs",
    "when in doubt, delete dead code",
]

LEGENDARY = [
    "legendary drop: one-line fix, first try",
    "legendary drop: every flaky test passes cleanly",
    "legendary drop: zero merge conflicts this sprint",
]

_LEGENDARY_CHANCE = 0.05


def pick_fortune() -> str:
    """Return a random fortune string (with a small legendary chance)."""
    if random.random() < _LEGENDARY_CHANCE:  # noqa: S311
        return random.choice(LEGENDARY)  # noqa: S311
    return random.choice(FORTUNES)  # noqa: S311


__all__ = ["FORTUNES", "LEGENDARY", "pick_fortune"]
