"""Slash-command handler for the Karna REPL.

Slash commands start with ``/`` and control the session
(model switching, conversation management, etc.) without being
sent to the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from karna.config import KarnaConfig, save_config
from karna.models import Conversation

if TYPE_CHECKING:
    from karna.sessions.cost import CostTracker
    from karna.sessions.db import SessionDB

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
        SlashCommand("compact", "/compact", "Summarize older messages to free context space"),
        SlashCommand("tools", "/tools", "List available tools"),
        SlashCommand("system", "/system <prompt>", "Set the system prompt"),
        SlashCommand("sessions", "/sessions", "Show last 5 sessions from history"),
        SlashCommand("resume", "/resume <id>", "Resume a previous session"),
        SlashCommand("paste", "/paste", "Read clipboard and insert as user message"),
        SlashCommand("copy", "/copy", "Copy last assistant response to clipboard"),
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


def _cmd_cost(console: Console, session_cost: SessionCost, cost_tracker: "CostTracker | None" = None, **_kw) -> None:
    # Prefer the persistent CostTracker if available
    if cost_tracker is not None:
        summary = cost_tracker.get_session_summary()
        console.print(
            Panel(
                f"[bright_black]Input tokens:[/]  {summary['input_tokens']:,}\n"
                f"[bright_black]Output tokens:[/] {summary['output_tokens']:,}\n"
                f"[bright_black]Session cost:[/]  ${summary['cost_usd']:.4f}",
                title="[bold #87CEEB]Session Cost[/]",
                border_style="#3C73BD",
                expand=False,
            )
        )
    else:
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


def _cmd_compact(
    console: Console,
    conversation: Conversation,
    config: KarnaConfig,
    **_kw,
) -> None:
    """Manually trigger conversation compaction."""
    import asyncio

    from karna.compaction.compactor import Compactor, _estimate_tokens
    from karna.providers import get_provider, resolve_model

    if len(conversation.messages) <= 6:
        console.print("[bright_black]Not enough messages to compact.[/bright_black]")
        return

    # Estimate tokens before compaction
    tokens_before = _estimate_tokens(conversation.messages)
    msg_count_before = len(conversation.messages)

    try:
        # Resolve the current provider for summarization
        model_spec = (
            f"{config.active_provider}:{config.active_model}"
            if ":" not in (config.active_model or "")
            else config.active_model
        )
        provider_name, model_name = resolve_model(model_spec)
        provider = get_provider(provider_name)
        provider.model = model_name

        compactor = Compactor(provider, threshold=0.0)  # threshold=0 forces compaction

        # Default context window (generous)
        context_window = 200_000

        # Run the compaction
        loop = asyncio.get_event_loop()
        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                compactor.compact(conversation, context_window),
                loop,
            )
            future.result(timeout=60)
        else:
            asyncio.run(compactor.compact(conversation, context_window))

    except Exception as exc:
        console.print(f"[red]Compaction failed: {type(exc).__name__}: {exc}[/red]")
        return

    tokens_after = _estimate_tokens(conversation.messages)
    msg_count_after = len(conversation.messages)
    saved = tokens_before - tokens_after

    console.print(
        Panel(
            f"[bright_black]Messages:[/] {msg_count_before} -> {msg_count_after}\n"
            f"[bright_black]Est. tokens:[/] ~{tokens_before:,} -> ~{tokens_after:,} "
            f"([green]-{saved:,}[/green])",
            title="[bold #87CEEB]Compaction Complete[/]",
            border_style="#3C73BD",
            expand=False,
        )
    )


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


def _cmd_sessions(console: Console, session_db: "SessionDB | None" = None, **_kw) -> None:
    if session_db is None:
        console.print("[bright_black]Session database not available.[/bright_black]")
        return
    sessions = session_db.list_sessions(limit=5)
    if not sessions:
        console.print("[bright_black]No sessions found.[/bright_black]")
        return
    table = Table(show_header=True, header_style="bold #87CEEB", border_style="#3C73BD", expand=False)
    table.add_column("ID", style="cyan")
    table.add_column("Started", style="green")
    table.add_column("Model", style="white")
    table.add_column("Cost", justify="right", style="yellow")
    for s in sessions:
        started = s["started_at"][:19].replace("T", " ")
        cost = f"${s['total_cost_usd']:.4f}"
        table.add_row(s["id"], started, s.get("model", ""), cost)
    console.print(table)


def _cmd_paste(console: Console, conversation: Conversation, **_kw) -> str | None:
    """Read clipboard and return content to be injected as user message."""
    import asyncio

    from karna.tools.clipboard import ClipboardTool

    tool = ClipboardTool()
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're in a sync context called from an async REPL — use run_coroutine_threadsafe
            future = asyncio.run_coroutine_threadsafe(tool.execute(action="read"), loop)
            result = future.result(timeout=10)
        else:
            result = asyncio.run(tool.execute(action="read"))
    except Exception as exc:
        console.print(f"[red]Failed to read clipboard: {exc}[/red]")
        return None

    if result.startswith("[error]") or result == "(clipboard is empty)":
        console.print(f"[bright_black]{result}[/bright_black]")
        return None

    # Show a preview of what was pasted
    preview = result[:100]
    if len(result) > 100:
        preview += "..."
    console.print(f"[bright_black]Pasted from clipboard ({len(result)} chars): {preview}[/bright_black]")

    # Return the clipboard content — the REPL should inject this as a user message.
    # We store it on the conversation as a signal.
    return result


def _cmd_copy(console: Console, conversation: Conversation, **_kw) -> None:
    """Copy the last assistant response to clipboard."""
    import asyncio

    from karna.tools.clipboard import ClipboardTool

    # Find the last assistant message
    last_assistant = None
    for msg in reversed(conversation.messages):
        if msg.role == "assistant" and msg.content:
            last_assistant = msg.content
            break

    if last_assistant is None:
        console.print("[bright_black]No assistant response to copy.[/bright_black]")
        return

    tool = ClipboardTool()
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(tool.execute(action="write", content=last_assistant), loop)
            result = future.result(timeout=10)
        else:
            result = asyncio.run(tool.execute(action="write", content=last_assistant))
    except Exception as exc:
        console.print(f"[red]Failed to copy to clipboard: {exc}[/red]")
        return

    console.print(f"[green]{result}[/green]")


def _cmd_resume(console: Console, args: str, session_db: "SessionDB | None" = None, **_kw) -> None:
    if session_db is None:
        console.print("[bright_black]Session database not available.[/bright_black]")
        return
    sid = args.strip() if args else None
    if not sid:
        sid = session_db.get_latest_session_id()
    if not sid:
        console.print("[bright_black]No sessions to resume.[/bright_black]")
        return
    conv = session_db.resume_session(sid)
    if conv is None:
        console.print(f"[red]Session not found: {sid}[/red]")
        return
    console.print(f"[yellow]Use [bold]nellie resume {sid}[/bold] to resume a session with full context.[/yellow]")


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
    "sessions": _cmd_sessions,
    "resume": _cmd_resume,
    "paste": _cmd_paste,
    "copy": _cmd_copy,
}


def handle_slash_command(
    raw_input: str,
    console: Console,
    config: KarnaConfig,
    conversation: Conversation,
    session_cost: SessionCost | None = None,
    tool_names: list[str] | None = None,
    session_db: "SessionDB | None" = None,
    cost_tracker: "CostTracker | None" = None,
) -> str | None:
    """Parse and dispatch a slash command.

    *raw_input* is the full user string including the leading ``/``.

    Returns a string if the command produces text to inject as a user
    message (e.g. ``/paste``), otherwise ``None``.
    """
    stripped = raw_input.strip().lstrip("/")
    parts = stripped.split(None, 1)
    cmd_name = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""

    handler = _HANDLERS.get(cmd_name)
    if handler is None:
        console.print(f"[red]Unknown command: /{cmd_name}[/red]  (type [bold]/help[/bold] to list commands)")
        return None

    result = handler(
        console=console,
        config=config,
        conversation=conversation,
        session_cost=session_cost or SessionCost(),
        tool_names=tool_names or [],
        args=args,
        session_db=session_db,
        cost_tracker=cost_tracker,
    )
    return result
