"""Startup banner printed when the REPL launches.

Displays version, active model, loaded tools, and a quick-help hint
inside a Rich panel with Kaeva-branded colours.
"""

from __future__ import annotations

from typing import Sequence

from rich.console import Console
from rich.panel import Panel

from karna import __version__
from karna.config import KarnaConfig
from karna.tui.themes import BRAND_BLUE


def print_banner(
    console: Console,
    config: KarnaConfig,
    tool_names: Sequence[str] = (),
) -> None:
    """Render the startup banner to *console*."""
    model_label = f"{config.active_provider}:{config.active_model}"
    tool_count = len(tool_names)

    lines = [
        f"[bold #87CEEB]karna[/bold #87CEEB] [dim]v{__version__}[/dim]",
        f"[bright_black]model:[/bright_black]  [white]{model_label}[/white]",
        f"[bright_black]tools:[/bright_black]  [white]{tool_count} loaded[/white]",
        "[bright_black]/help for commands[/bright_black]",
    ]

    panel = Panel(
        "\n".join(lines),
        border_style=BRAND_BLUE,
        padding=(0, 2),
        expand=False,
    )
    console.print()
    console.print(panel)
    console.print()
