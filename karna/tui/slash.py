"""Slash-command handler for the Karna REPL.

Slash commands start with ``/`` and control the session
(model switching, conversation management, etc.) without being
sent to the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from karna.config import KarnaConfig, save_config
from karna.models import Conversation


# --------------------------------------------------------------------------- #
#  Session-level cost tracking (populated by output.py)
# --------------------------------------------------------------------------- #

@dataclass
class SessionCost:
    """Mutable accumulator for per-session token/cost tracking."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_usd: float = 0.0

    def add(self, prompt: int = 0, completion: int = 0, usd: float = 0.0) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_usd += usd


# --------------------------------------------------------------------------- #
#  Command definitions
# --------------------------------------------------------------------------- #

@dataclass
class SlashCommand:
    """Metadata for a single slash command."""

    name: str
    usage: str
    help_text: str
    handler: Callable[..., None] = field(repr=False, default=lambda *a, **k: None)


def _build_commands() -> dict[str, SlashCommand]:
    """Return the canonical command table (handlers are bound later)."""
    cmds: list[SlashCommand] = [
        SlashCommand("help", "/help", "List available commands"),
        SlashCommand("model", "/model <provider:model>", "Switch model mid-conversation"),
        SlashCommand("clear", "/clear", "Reset conversation history"),
        SlashCommand("history", "/history", "Show conversation so far"),
        SlashCommand("cost", "/cost", "Show total token usage and cost this session"),
        SlashCommand("exit", "/exit", "Exit the REPL"),
        SlashCommand("quit", "/quit", "Exit the REPL"),
        SlashCommand("compact", "/compact", "Trigger conversation compaction (stub)"),
        SlashCommand("tools", "/tools", "List available tools"),
        SlashCommand("system", "/system <prompt>", "Set the system prompt"),
    ]
    return {c.name: c for c in cmds}


COMMANDS = _build_commands()


# --------------------------------------------------------------------------- #
#  Handler implementations
# --------------------------------------------------------------------------- #

def _cmd_help(console: Console, **_kw) -> None:  # type: ignore[no-untyped-def]
    table = Table(show_header=True, header_style="bold #87CEEB", border_style="#3C73BD", expand=False)
    table.add_column("Command", style="white")
    table.add_column("Description", style="bright_black")
    for cmd in COMMANDS.values():
        if cmd.name == "quit":
            continue  # don't duplicate /exit
        table.add_row(cmd.usage, cmd.help_text)
    console.print(table)


def _cmd_model(console: Console, config: KarnaConfig, args: str, conversation: Conversation, **_kw) -> None:
    if not args:
        console.print(f"[bright_black]Current model:[/] [white]{config.active_provider}:{config.active_model}[/]")
        return
    if ":" not in args:
        console.print("[red]Usage: /model provider:model_name[/red]")
        return
    provider, model = args.split(":", 1)
    config.active_provider = provider.strip()
    config.active_model = model.strip()
    conversation.provider = config.active_provider
    conversation.model = config.active_model
    save_config(config)
    console.print(f"[green]Switched to [bold]{config.active_provider}:{config.active_model}[/bold][/green]")


def _cmd_clear(console: Console, conversation: Conversation, **_kw) -> None:
    conversation.messages.clear()
    console.print("[green]Conversation cleared.[/green]")


def _cmd_history(console: Console, conversation: Conversation, **_kw) -> None:
    if not conversation.messages:
        console.print("[bright_black]No messages yet.[/bright_black]")
        return
    for msg in conversation.messages:
        role_style = {
            "user": "white",
            "assistant": "#87CEEB",
            "system": "yellow",
            "tool": "dim green",
        }.get(msg.role, "white")
        label = msg.role.capitalize()
        content_preview = msg.content[:200]
        if len(msg.content) > 200:
            content_preview += "..."
        console.print(f"[bold {role_style}]{label}:[/bold {role_style}] {content_preview}")


def _cmd_cost(console: Console, session_cost: SessionCost, **_kw) -> None:
    console.print(
        Panel(
            f"[bright_black]Prompt tokens:[/]  {session_cost.prompt_tokens:,}\n"
            f"[bright_black]Output tokens:[/] {session_cost.completion_tokens:,}\n"
            f"[bright_black]Total cost:[/]    ${session_cost.total_usd:.4f}",
            title="[bold #87CEEB]Session Cost[/]",
            border_style="#3C73BD",
            expand=False,
        )
    )


def _cmd_exit(**_kw) -> None:
    raise SystemExit(0)


def _cmd_compact(console: Console, **_kw) -> None:
    console.print("[yellow]Compaction not yet implemented — coming in Phase 4.[/yellow]")


def _cmd_tools(console: Console, tool_names: list[str], **_kw) -> None:
    if not tool_names:
        console.print("[bright_black]No tools loaded.[/bright_black]")
        return
    table = Table(show_header=True, header_style="bold #87CEEB", border_style="#3C73BD", expand=False)
    table.add_column("#", style="bright_black", justify="right")
    table.add_column("Tool", style="white")
    for i, name in enumerate(sorted(tool_names), 1):
        table.add_row(str(i), name)
    console.print(table)


def _cmd_system(console: Console, config: KarnaConfig, args: str, **_kw) -> None:
    if not args:
        console.print(f"[bright_black]System prompt:[/] [white]{config.system_prompt}[/white]")
        return
    config.system_prompt = args
    save_config(config)
    console.print("[green]System prompt updated.[/green]")


# --------------------------------------------------------------------------- #
#  Dispatcher
# --------------------------------------------------------------------------- #

_HANDLERS: dict[str, Callable[..., None]] = {
    "help": _cmd_help,
    "model": _cmd_model,
    "clear": _cmd_clear,
    "history": _cmd_history,
    "cost": _cmd_cost,
    "exit": _cmd_exit,
    "quit": _cmd_exit,
    "compact": _cmd_compact,
    "tools": _cmd_tools,
    "system": _cmd_system,
}


def handle_slash_command(
    raw_input: str,
    console: Console,
    config: KarnaConfig,
    conversation: Conversation,
    session_cost: SessionCost | None = None,
    tool_names: list[str] | None = None,
) -> None:
    """Parse and dispatch a slash command.

    *raw_input* is the full user string including the leading ``/``.
    """
    stripped = raw_input.strip().lstrip("/")
    parts = stripped.split(None, 1)
    cmd_name = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""

    handler = _HANDLERS.get(cmd_name)
    if handler is None:
        console.print(f"[red]Unknown command: /{cmd_name}[/red]  (type [bold]/help[/bold] to list commands)")
        return

    handler(
        console=console,
        config=config,
        conversation=conversation,
        session_cost=session_cost or SessionCost(),
        tool_names=tool_names or [],
        args=args,
    )
