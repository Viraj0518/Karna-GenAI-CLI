"""Catch missing imports in the TUI REPL before they hit a live user.

A real user ran ``nellie`` and got a ``NameError: BRAILLE_FRAMES is not
defined`` the moment the status-bar tick fired. The status-bar function
is a closure built inside ``_build_application``, so it's not reached by
the other TUI unit tests — this file exercises it directly.
"""

from __future__ import annotations

import importlib


def test_repl_module_imports_cleanly() -> None:
    """If any identifier the repl references at module load is missing, this
    will raise NameError/ImportError immediately."""
    mod = importlib.import_module("karna.tui.repl")
    # Every symbol the status bar reaches for must be resolvable at module
    # level. Re-import is fine — it's idempotent after the first time.
    for name in ("BRAILLE_FRAMES", "FACES", "VERBS", "LONG_RUN_CHARMS"):
        assert hasattr(mod, name), f"repl.py is missing {name!r} — status-bar will crash"


def test_status_bar_symbols_resolve_at_runtime() -> None:
    """Simulate the status-bar's actual identifier accesses."""
    from karna.tui.repl import BRAILLE_FRAMES, FACES, LONG_RUN_CHARMS, VERBS

    assert len(BRAILLE_FRAMES) > 0
    assert len(FACES) > 0
    assert len(VERBS) > 0
    assert len(LONG_RUN_CHARMS) > 0

    # The exact indexing pattern the status bar uses:
    now = 1_700_000_000.0
    frame = BRAILLE_FRAMES[int(now * 10) % len(BRAILLE_FRAMES)]
    assert isinstance(frame, str) and len(frame) >= 1
