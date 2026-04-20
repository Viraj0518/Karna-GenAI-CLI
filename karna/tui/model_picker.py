"""Interactive model picker for the Nellie TUI.

When the user types ``/model`` without arguments, this module presents a
searchable, navigable list of available models grouped by provider.

Falls back to a simple numbered list if the full prompt_toolkit dialog
layout is not available (e.g. inside the split-pane Application).

Public API:
    pick_model(console) -> str | None   — returns "provider:model" or None
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table
from rich.text import Text

from karna.tui.design_tokens import SEMANTIC

if TYPE_CHECKING:
    pass

# -- Known models with metadata -----------------------------------------------

_MODEL_CATALOG: list[dict[str, str]] = [
    # OpenRouter
    {"provider": "openrouter", "model": "openrouter/auto", "ctx": "varies", "notes": "auto-route"},
    {"provider": "openrouter", "model": "openai/gpt-4o", "ctx": "128k", "notes": ""},
    {"provider": "openrouter", "model": "openai/gpt-4o-mini", "ctx": "128k", "notes": "fast, cheap"},
    {"provider": "openrouter", "model": "anthropic/claude-sonnet-4-20250514", "ctx": "200k", "notes": "balanced"},
    {"provider": "openrouter", "model": "anthropic/claude-opus-4-20250514", "ctx": "200k", "notes": "strongest"},
    {"provider": "openrouter", "model": "google/gemini-2.5-pro-preview", "ctx": "1M", "notes": "long ctx"},
    {"provider": "openrouter", "model": "google/gemini-2.5-flash-preview", "ctx": "1M", "notes": "fast"},
    {"provider": "openrouter", "model": "meta-llama/llama-4-maverick", "ctx": "1M", "notes": "open"},
    {"provider": "openrouter", "model": "deepseek/deepseek-r1", "ctx": "64k", "notes": "reasoning"},
    {"provider": "openrouter", "model": "deepseek/deepseek-chat-v3-0324", "ctx": "64k", "notes": "chat"},
    # Anthropic direct
    {"provider": "anthropic", "model": "claude-opus-4-20250514", "ctx": "200k", "notes": "strongest"},
    {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "ctx": "200k", "notes": "balanced"},
    {"provider": "anthropic", "model": "claude-haiku-3.5", "ctx": "200k", "notes": "fast, cheap"},
    # OpenAI direct
    {"provider": "openai", "model": "gpt-4o", "ctx": "128k", "notes": "flagship"},
    {"provider": "openai", "model": "gpt-4o-mini", "ctx": "128k", "notes": "fast, cheap"},
    {"provider": "openai", "model": "o3-mini", "ctx": "128k", "notes": "reasoning"},
    {"provider": "openai", "model": "o3", "ctx": "128k", "notes": "reasoning"},
    {"provider": "openai", "model": "o4-mini", "ctx": "128k", "notes": "reasoning"},
    # Vertex
    {"provider": "vertex", "model": "gemini-2.5-pro-preview", "ctx": "1M", "notes": "Google Cloud"},
    {"provider": "vertex", "model": "gemini-2.5-flash-preview", "ctx": "1M", "notes": "Google Cloud"},
    # Local
    {"provider": "local", "model": "llama3", "ctx": "8k", "notes": "Ollama"},
    {"provider": "local", "model": "mistral", "ctx": "32k", "notes": "Ollama"},
    {"provider": "local", "model": "codellama", "ctx": "16k", "notes": "Ollama"},
]


def _filter_catalog(query: str) -> list[dict[str, str]]:
    """Filter the model catalog by a case-insensitive substring match."""
    if not query:
        return list(_MODEL_CATALOG)
    q = query.lower()
    return [m for m in _MODEL_CATALOG if q in m["model"].lower() or q in m["provider"].lower() or q in m.get("notes", "").lower()]


def render_model_table(
    console: Console,
    query: str = "",
    *,
    highlight_index: int | None = None,
) -> list[dict[str, str]]:
    """Render the model picker table and return the filtered list.

    If *highlight_index* is given, that row is visually highlighted.
    Returns the filtered model list so the caller knows what index
    maps to what.
    """
    cyan = SEMANTIC.get("accent.cyan", "#87CEEB")
    border = SEMANTIC.get("border.accent", "#3C73BD")
    brand = SEMANTIC.get("accent.brand", "#3C73BD")

    filtered = _filter_catalog(query)

    table = Table(
        show_header=True,
        header_style=f"bold {cyan}",
        border_style=border,
        expand=False,
        title=Text("Model Picker", style=f"bold {cyan}"),
        title_justify="left",
    )
    table.add_column("#", style="bright_black", justify="right", width=3)
    table.add_column("Provider", style=brand, width=12)
    table.add_column("Model", style="white", min_width=30)
    table.add_column("Context", style="bright_black", width=8)
    table.add_column("Notes", style="bright_black", width=16)

    for i, entry in enumerate(filtered):
        num_style = "bold white" if i == highlight_index else "bright_black"
        model_style = f"bold {cyan}" if i == highlight_index else "white"
        table.add_row(
            f"[{num_style}]{i + 1}[/]",
            entry["provider"],
            f"[{model_style}]{entry['model']}[/]",
            entry.get("ctx", ""),
            entry.get("notes", ""),
        )

    console.print(table)

    if query:
        console.print(f"[bright_black]  Filter: {query}  ({len(filtered)} matches)[/bright_black]")
    console.print(
        "[bright_black]  Enter a number to select, type to filter, "
        "or press Enter/Esc to cancel.[/bright_black]"
    )

    return filtered


def pick_model_numbered(console: Console) -> str | None:
    """Non-interactive model picker: show a numbered list, read a number.

    This is the fallback used inside the split-pane TUI where we cannot
    run a nested prompt_toolkit Application.  The output goes through
    the ``console.print()`` path (which routes to the output pane in
    split-pane mode), and the selection is entered via the normal input
    buffer.

    Returns ``"provider:model"`` or ``None`` if cancelled.
    """
    filtered = render_model_table(console)
    return _resolve_selection(filtered)


def _resolve_selection(
    filtered: list[dict[str, str]],
    choice: str | None = None,
) -> str | None:
    """Given a user choice string, resolve it to a provider:model.

    If *choice* is a number, look up the matching entry.
    If it looks like a provider:model, return it directly.
    Returns None on cancellation or invalid input.
    """
    if not choice:
        return None

    choice = choice.strip()

    # Numeric selection
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(filtered):
            entry = filtered[idx]
            return f"{entry['provider']}:{entry['model']}"
        return None

    # Direct model string
    if ":" in choice or "/" in choice:
        return choice

    return None


def get_model_catalog() -> list[dict[str, str]]:
    """Return the full model catalog for external consumers."""
    return list(_MODEL_CATALOG)
