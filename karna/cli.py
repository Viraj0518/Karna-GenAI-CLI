"""Nellie CLI — entry point exposed as ``nellie``.

Commands
--------
* ``nellie``              — enter the interactive REPL
* ``nellie auth login``   — authenticate with a provider
* ``nellie model``        — show / set the active model
* ``nellie config show``  — dump current configuration
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich import print as rprint
from rich.table import Table

from karna import __version__
from karna.config import load_config, save_config

# --------------------------------------------------------------------------- #
#  App & sub-groups
# --------------------------------------------------------------------------- #

app = typer.Typer(
    name="nellie",
    help="Nellie — Karna's internal AI agent harness.",
    no_args_is_help=False,
    invoke_without_command=True,
)

auth_app = typer.Typer(help="Authentication commands.")
model_app = typer.Typer(help="Model selection commands.", invoke_without_command=True)
config_app = typer.Typer(help="Configuration commands.")
mcp_app = typer.Typer(help="MCP server management commands.")

history_app = typer.Typer(help="Session history commands.", invoke_without_command=True)
cost_app = typer.Typer(help="Cost tracking commands.")

app.add_typer(auth_app, name="auth")
app.add_typer(model_app, name="model")
app.add_typer(config_app, name="config")
app.add_typer(mcp_app, name="mcp")
app.add_typer(history_app, name="history")
app.add_typer(cost_app, name="cost")


# --------------------------------------------------------------------------- #
#  Version callback
# --------------------------------------------------------------------------- #

def _version_callback(value: bool) -> None:
    if value:
        rprint(f"nellie {__version__}")
        raise typer.Exit()


# --------------------------------------------------------------------------- #
#  Root command — REPL stub
# --------------------------------------------------------------------------- #

@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Nellie — Karna's internal AI agent harness. CLI binary: nellie."""
    if ctx.invoked_subcommand is None:
        # No subcommand → launch the interactive REPL
        from karna.tui import run_repl

        cfg = load_config()
        asyncio.run(run_repl(cfg))


@app.command(hidden=True)
def repl() -> None:
    """Enter the interactive REPL."""
    from karna.tui import run_repl

    cfg = load_config()
    asyncio.run(run_repl(cfg))


# --------------------------------------------------------------------------- #
#  Auth commands
# --------------------------------------------------------------------------- #

@auth_app.command("login")
def auth_login(
    provider: str = typer.Argument(..., help="Provider name (e.g. openrouter, openai, anthropic)"),
) -> None:
    """Authenticate with a model provider."""
    rprint(f"[yellow]auth login for [bold]{provider}[/bold] — not yet implemented.[/yellow]")


# --------------------------------------------------------------------------- #
#  Model commands
# --------------------------------------------------------------------------- #

@model_app.callback()
def model_root(
    ctx: typer.Context,
) -> None:
    """Show or set the active model."""
    # If a subcommand was invoked, let it run; otherwise show current model.
    if ctx.invoked_subcommand is not None:
        return
    cfg = load_config()
    rprint(f"[bold]Active model:[/bold] {cfg.active_provider}/{cfg.active_model}")


@model_app.command("set")
def model_set(
    model_spec: str = typer.Argument(
        ...,
        help="Model spec as provider:model (e.g. openrouter:meta-llama/llama-3.3-70b-instruct)",
    ),
) -> None:
    """Set the active model."""
    if ":" not in model_spec:
        rprint("[red]Model spec must be provider:model (e.g. openrouter:meta-llama/llama-3.3-70b-instruct)[/red]")
        raise typer.Exit(code=1)

    provider, model = model_spec.split(":", 1)
    cfg = load_config()
    cfg.active_provider = provider
    cfg.active_model = model
    save_config(cfg)
    rprint(f"[green]Active model set to [bold]{provider}/{model}[/bold][/green]")


# --------------------------------------------------------------------------- #
#  Config commands
# --------------------------------------------------------------------------- #

@config_app.command("show")
def config_show() -> None:
    """Dump the current Nellie configuration."""
    cfg = load_config()
    table = Table(title="Nellie Configuration")
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="green")
    for key, value in cfg.model_dump().items():
        table.add_row(key, str(value))
    rprint(table)


# --------------------------------------------------------------------------- #
#  History commands
# --------------------------------------------------------------------------- #

@history_app.callback(invoke_without_command=True)
def history_root(ctx: typer.Context) -> None:
    """List recent sessions."""
    if ctx.invoked_subcommand is not None:
        return
    from karna.sessions.db import SessionDB

    db = SessionDB()
    sessions = db.list_sessions(limit=20)
    db.close()

    if not sessions:
        rprint("[bright_black]No sessions found.[/bright_black]")
        return

    table = Table(title="Recent Sessions")
    table.add_column("ID", style="cyan")
    table.add_column("Started", style="green")
    table.add_column("Model", style="white")
    table.add_column("Tokens", justify="right", style="bright_black")
    table.add_column("Cost", justify="right", style="yellow")
    table.add_column("Summary", style="bright_black", max_width=40)

    for s in sessions:
        tokens = f"{s['total_input_tokens'] + s['total_output_tokens']:,}"
        cost = f"${s['total_cost_usd']:.4f}"
        started = s["started_at"][:19].replace("T", " ")
        summary = (s.get("summary") or "")[:40]
        table.add_row(s["id"], started, s.get("model", ""), tokens, cost, summary)

    rprint(table)


@history_app.command("search")
def history_search(
    query: str = typer.Argument(..., help="Full-text search query"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
) -> None:
    """Search across all session messages (FTS5)."""
    from karna.sessions.db import SessionDB

    db = SessionDB()
    results = db.search(query, limit=limit)
    db.close()

    if not results:
        rprint(f"[bright_black]No results for:[/bright_black] {query}")
        return

    table = Table(title=f'Search: "{query}"')
    table.add_column("Session", style="cyan")
    table.add_column("Role", style="yellow")
    table.add_column("Content", style="white", max_width=80)
    table.add_column("Model", style="bright_black")

    for r in results:
        content = (r.get("content") or "")[:80]
        if len(r.get("content") or "") > 80:
            content += "..."
        table.add_row(
            r.get("session_id", ""),
            r.get("role", ""),
            content,
            r.get("model", ""),
        )

    rprint(table)


@history_app.command("show")
def history_show(
    session_id: str = typer.Argument(..., help="Session ID to display"),
) -> None:
    """Replay a session."""
    from karna.sessions.db import SessionDB

    db = SessionDB()
    session = db.get_session(session_id)
    if session is None:
        rprint(f"[red]Session not found: {session_id}[/red]")
        db.close()
        raise typer.Exit(code=1)

    messages = db.get_session_messages(session_id)
    db.close()

    rprint(f"\n[bold]Session {session_id}[/bold]")
    rprint(f"  Model: {session.get('model', 'unknown')}")
    rprint(f"  Started: {session['started_at'][:19].replace('T', ' ')}")
    ended = session.get("ended_at")
    if ended:
        rprint(f"  Ended:   {ended[:19].replace('T', ' ')}")
    rprint(f"  Cost:    ${session['total_cost_usd']:.4f}")
    rprint()

    for msg in messages:
        role = msg["role"]
        style = {"user": "white", "assistant": "#87CEEB", "system": "yellow", "tool": "dim green"}.get(role, "white")
        content = msg.get("content") or ""
        preview = content[:300]
        if len(content) > 300:
            preview += "..."
        rprint(f"[bold {style}]{role.capitalize()}:[/bold {style}] {preview}")


@history_app.command("delete")
def history_delete(
    session_id: str = typer.Argument(..., help="Session ID to delete"),
) -> None:
    """Delete a session and its messages."""
    from karna.sessions.db import SessionDB

    db = SessionDB()
    deleted = db.delete_session(session_id)
    db.close()

    if deleted:
        rprint(f"[green]Deleted session {session_id}[/green]")
    else:
        rprint(f"[red]Session not found: {session_id}[/red]")


# --------------------------------------------------------------------------- #
#  Resume command
# --------------------------------------------------------------------------- #

@app.command()
def resume(
    session_id: str = typer.Argument(None, help="Session ID to resume (default: most recent)"),
) -> None:
    """Resume a previous session."""
    from karna.sessions.db import SessionDB

    db = SessionDB()
    if session_id is None:
        session_id = db.get_latest_session_id()
        if session_id is None:
            rprint("[red]No sessions to resume.[/red]")
            db.close()
            raise typer.Exit(code=1)

    session = db.get_session(session_id)
    if session is None:
        rprint(f"[red]Session not found: {session_id}[/red]")
        db.close()
        raise typer.Exit(code=1)

    conversation = db.resume_session(session_id)
    db.close()

    if conversation is None:
        rprint(f"[red]Failed to load session: {session_id}[/red]")
        raise typer.Exit(code=1)

    rprint(f"[green]Resuming session [bold]{session_id}[/bold] ({len(conversation.messages)} messages)[/green]")

    from karna.tui import run_repl

    cfg = load_config()
    # Override model/provider from the resumed session
    if session.get("model"):
        cfg.active_model = session["model"]
    if session.get("provider"):
        cfg.active_provider = session["provider"]

    asyncio.run(run_repl(cfg, resume_conversation=conversation, resume_session_id=session_id))


# --------------------------------------------------------------------------- #
#  Cost command
# --------------------------------------------------------------------------- #

@cost_app.callback(invoke_without_command=True)
def cost_root(ctx: typer.Context) -> None:
    """Show cost summary."""
    if ctx.invoked_subcommand is not None:
        return

    from karna.sessions.db import SessionDB
    from karna.sessions.cost import CostTracker

    db = SessionDB()
    tracker = CostTracker(db, session_id="", model="", provider="")

    today = tracker.get_today_summary()
    weekly = tracker.get_weekly_summary()
    total = tracker.get_total_summary()
    by_model = tracker.get_by_model(days=30)
    db.close()

    table = Table(title="Cost Summary")
    table.add_column("Period", style="cyan")
    table.add_column("Sessions", justify="right", style="white")
    table.add_column("Input Tokens", justify="right", style="bright_black")
    table.add_column("Output Tokens", justify="right", style="bright_black")
    table.add_column("Cost", justify="right", style="yellow")

    for label, data in [("Today", today), ("This Week", weekly), ("All Time", total)]:
        table.add_row(
            label,
            str(data.get("session_count", 0)),
            f"{data.get('input_tokens', 0):,}",
            f"{data.get('output_tokens', 0):,}",
            f"${data.get('cost_usd', 0):.4f}",
        )

    rprint(table)

    if by_model:
        model_table = Table(title="Cost by Model (last 30 days)")
        model_table.add_column("Model", style="cyan")
        model_table.add_column("Sessions", justify="right", style="white")
        model_table.add_column("Input Tokens", justify="right", style="bright_black")
        model_table.add_column("Output Tokens", justify="right", style="bright_black")
        model_table.add_column("Cost", justify="right", style="yellow")

        for model_name, data in by_model.items():
            model_table.add_row(
                model_name or "unknown",
                str(data.get("session_count", 0)),
                f"{data.get('input_tokens', 0):,}",
                f"{data.get('output_tokens', 0):,}",
                f"${data.get('cost_usd', 0):.4f}",
            )

        rprint(model_table)


# --------------------------------------------------------------------------- #
#  MCP commands
# --------------------------------------------------------------------------- #

@mcp_app.command("add")
def mcp_add(
    name: str = typer.Argument(..., help="Server name"),
    command: str = typer.Argument(..., help="Command to launch the server"),
    args: list[str] = typer.Argument(None, help="Additional arguments for the server command"),
) -> None:
    """Add an MCP server to the configuration."""
    from karna.tools.mcp import add_mcp_server

    add_mcp_server(name, command, args or [])
    rprint(f"[green]Added MCP server [bold]{name}[/bold] ({command})[/green]")


@mcp_app.command("list")
def mcp_list() -> None:
    """List configured MCP servers."""
    from karna.tools.mcp import list_mcp_servers

    servers = list_mcp_servers()
    if not servers:
        rprint("[bright_black]No MCP servers configured.[/bright_black]")
        return

    table = Table(title="MCP Servers")
    table.add_column("Name", style="cyan")
    table.add_column("Command", style="white")
    table.add_column("Args", style="bright_black")
    for name, cfg in servers.items():
        table.add_row(name, cfg.get("command", ""), " ".join(cfg.get("args", [])))
    rprint(table)


@mcp_app.command("remove")
def mcp_remove(
    name: str = typer.Argument(..., help="Server name to remove"),
) -> None:
    """Remove an MCP server from the configuration."""
    from karna.tools.mcp import remove_mcp_server

    if remove_mcp_server(name):
        rprint(f"[green]Removed MCP server [bold]{name}[/bold][/green]")
    else:
        rprint(f"[red]MCP server not found: {name}[/red]")
        raise typer.Exit(code=1)


@mcp_app.command("test")
def mcp_test(
    name: str = typer.Argument(..., help="Server name to test"),
) -> None:
    """Test an MCP server connection (connect + list tools)."""
    from karna.tools.mcp import MCPClientTool

    async def _run() -> None:
        client = MCPClientTool()
        if name not in client._server_configs:
            rprint(f"[red]MCP server not found: {name}[/red]")
            raise typer.Exit(code=1)

        rprint(f"[bright_black]Connecting to {name}...[/bright_black]")
        result = await client._connect(name)
        rprint(result)

        conn = client.servers.get(name)
        if conn and conn.tools:
            table = Table(title=f"Tools from {name}")
            table.add_column("Tool", style="cyan")
            table.add_column("Description", style="white")
            for tool in conn.tools:
                table.add_row(tool.get("name", "?"), tool.get("description", ""))
            rprint(table)

        await client.shutdown()

    asyncio.run(_run())


# --------------------------------------------------------------------------- #
#  Init command
# --------------------------------------------------------------------------- #

AI_CONFIG_FILES = ["CLAUDE.md", ".cursorrules", ".github/copilot-instructions.md"]


@app.command()
def init(
    provider: str = typer.Option(None, help="Default provider"),
    model: str = typer.Option(None, help="Default model"),
) -> None:
    """Initialize Nellie for this project.

    Creates KARNA.md with project-specific instructions.
    Detects existing configs (CLAUDE.md, .cursorrules) and imports.
    """
    from karna.init import detect_project_type, generate_karna_md_for_path

    cwd = Path.cwd()

    # Check for existing AI configs
    existing = [name for name in AI_CONFIG_FILES if (cwd / name).exists()]
    if existing:
        rprint(f"[bright_black]Found existing AI config: {', '.join(existing)}[/bright_black]")
        rprint("[bright_black]Nellie will read these automatically. Creating KARNA.md for additional instructions.[/bright_black]")

    # Detect project type
    project_type = detect_project_type(cwd)

    # Generate & write KARNA.md
    karna_md = cwd / "KARNA.md"
    if karna_md.exists():
        rprint("[yellow]KARNA.md already exists. Skipping.[/yellow]")
    else:
        template = generate_karna_md_for_path(cwd, project_type, provider, model)
        karna_md.write_text(template)
        rprint(f"[green]Created KARNA.md ({len(template)} bytes)[/green]")

    # Create .karna/ project dir
    project_dir = cwd / ".karna"
    project_dir.mkdir(exist_ok=True)
    (project_dir / ".gitignore").write_text("*\n")

    rprint("\n[green]\u2713 Project initialized. Run `nellie` to start.[/green]")


# --------------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------------- #

def main() -> None:
    """Entry point for the ``nellie`` console script."""
    app()


if __name__ == "__main__":
    main()
