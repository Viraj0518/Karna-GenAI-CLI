"""Interactive session picker for the Nellie TUI.

When the user types ``/resume`` without an ID, this module renders a
table of the last 10 sessions with metadata (ID, date, model, first
user message preview) and returns the selected session ID.

Public API:
    render_session_table(console, session_db) -> list[dict]
    resolve_session_choice(sessions, choice) -> str | None
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from karna.tui.design_tokens import SEMANTIC

if TYPE_CHECKING:
    from karna.sessions.db import SessionDB


def _first_user_message(session_db: "SessionDB", session_id: str) -> str:
    """Return the first user message content (preview) for a session."""
    messages = session_db.get_session_messages(session_id)
    for msg in messages:
        if msg.get("role") == "user" and msg.get("content"):
            content = msg["content"].strip()
            if len(content) > 60:
                return content[:57] + "..."
            return content
    return "(no messages)"


def render_session_table(
    console: Console,
    session_db: "SessionDB",
    *,
    limit: int = 10,
    highlight_index: int | None = None,
) -> list[dict[str, Any]]:
    """Render an interactive session picker table.

    Returns the list of session dicts so the caller can map a
    numeric choice to a session ID.
    """
    cyan = SEMANTIC.get("accent.cyan", "#87CEEB")
    border = SEMANTIC.get("border.accent", "#3C73BD")
    brand = SEMANTIC.get("accent.brand", "#3C73BD")

    sessions = session_db.list_sessions(limit=limit)
    if not sessions:
        console.print("[bright_black]No sessions found.[/bright_black]")
        return []

    table = Table(
        show_header=True,
        header_style=f"bold {cyan}",
        border_style=border,
        expand=False,
        title=Text("Resume Session", style=f"bold {cyan}"),
        title_justify="left",
    )
    table.add_column("#", style="bright_black", justify="right", width=3)
    table.add_column("ID", style=brand, width=14)
    table.add_column("Date", style="green", width=19)
    table.add_column("Model", style="white", width=20)
    table.add_column("Cost", style="yellow", justify="right", width=10)
    table.add_column("Preview", style="bright_black", max_width=40)

    for i, s in enumerate(sessions):
        started = s["started_at"][:19].replace("T", " ")
        cost = f"${s['total_cost_usd']:.4f}"
        preview = _first_user_message(session_db, s["id"])

        num_style = "bold white" if i == highlight_index else "bright_black"
        id_style = f"bold {cyan}" if i == highlight_index else brand

        table.add_row(
            f"[{num_style}]{i + 1}[/]",
            f"[{id_style}]{s['id']}[/]",
            started,
            s.get("model", ""),
            cost,
            preview,
        )

    console.print(table)
    console.print(
        "[bright_black]  Enter a number or session ID to resume, or press Enter/Esc to cancel.[/bright_black]"
    )

    return sessions


def resolve_session_choice(
    sessions: list[dict[str, Any]],
    choice: str | None,
) -> str | None:
    """Map a user's choice to a session ID.

    *choice* may be a 1-based index number or a session ID string.
    Returns the session ID or None if cancelled / invalid.
    """
    if not choice:
        return None

    choice = choice.strip()

    # Numeric selection
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(sessions):
            return sessions[idx]["id"]
        return None

    # Direct session ID
    for s in sessions:
        if s["id"] == choice:
            return s["id"]

    return None
