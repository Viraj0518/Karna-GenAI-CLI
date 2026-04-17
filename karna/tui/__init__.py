"""Karna TUI — Rich-based terminal interface with streaming output.

Public API:
    run_repl(config)  — the main interactive REPL loop
"""

from karna.tui.repl import run_repl

__all__ = ["run_repl"]
