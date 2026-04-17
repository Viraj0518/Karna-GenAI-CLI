"""REPL stub — Phase 2.

Provides a placeholder ``run_repl()`` that prints a message
and exits. Will be replaced with a full Rich-based REPL.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich import print as rprint

if TYPE_CHECKING:
    from karna.config import KarnaConfig


async def run_repl(config: "KarnaConfig") -> None:
    """Launch the interactive REPL (not yet implemented)."""
    rprint("[bold yellow]REPL not yet implemented — coming in Phase 2.[/bold yellow]")
    rprint(f"[dim]Active model: {config.active_provider}/{config.active_model}[/dim]")
