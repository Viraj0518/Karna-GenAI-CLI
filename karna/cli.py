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
from karna.config import effective_thinking, load_config, save_config

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
acp_app = typer.Typer(help="Agent Client Protocol (ACP) server commands.")

history_app = typer.Typer(help="Session history commands.", invoke_without_command=True)
cost_app = typer.Typer(help="Cost tracking commands.")
cron_app = typer.Typer(help="Scheduled agent jobs.", invoke_without_command=True)
index_app = typer.Typer(help="Knowledge base indexing commands.", invoke_without_command=True)

app.add_typer(auth_app, name="auth")
app.add_typer(model_app, name="model")
app.add_typer(config_app, name="config")
app.add_typer(mcp_app, name="mcp")
app.add_typer(acp_app, name="acp")
app.add_typer(history_app, name="history")
app.add_typer(cost_app, name="cost")
app.add_typer(cron_app, name="cron")
app.add_typer(index_app, name="index")


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
    api_key: str = typer.Option(
        None,
        "--key",
        "-k",
        help="API key (will prompt if not provided).",
    ),
) -> None:
    """Store credentials for a model provider."""
    from karna.auth.credentials import save_credential

    if not api_key:
        api_key = typer.prompt(f"API key for {provider}", hide_input=True)
    path = save_credential(provider, {"api_key": api_key})
    rprint(f"[green]Saved {provider} credential to {path}[/green]")


@auth_app.command("list")
def auth_list() -> None:
    """List providers that have stored credentials."""
    from karna.auth.credentials import list_credentials

    providers = list_credentials()
    if not providers:
        rprint("[bright_black]No credentials stored.[/bright_black]")
        return
    for p in providers:
        rprint(f"  {p}")


@auth_app.command("logout")
def auth_logout(
    provider: str = typer.Argument(..., help="Provider name to remove credentials for"),
) -> None:
    """Delete stored credentials for a provider."""
    from karna.auth.credentials import CREDENTIALS_DIR

    path = CREDENTIALS_DIR / f"{provider}.token.json"
    if not path.exists():
        rprint(f"[red]No credentials found for {provider}[/red]")
        raise typer.Exit(code=1)
    path.unlink()
    rprint(f"[green]Removed {provider} credential ({path})[/green]")


@auth_app.command("migrate")
def auth_migrate(
    keep_json: bool = typer.Option(
        False,
        "--keep-json",
        help="Don't delete the JSON files after migration",
    ),
) -> None:
    """Move JSON credentials into the OS keyring.

    Idempotent. Skips providers already in keyring. Prints a summary of
    what moved, what was left behind, and any errors.
    """
    from karna.auth import keyring_store

    if not keyring_store.is_available():
        rprint(
            "[red]OS keyring is not available on this system.[/red]\n"
            "Karna will continue using JSON storage at ~/.karna/credentials/.\n"
            "Install a Secret Service provider (Linux) or re-run on a platform "
            "with keyring support (macOS Keychain / Windows Credential Manager)."
        )
        raise typer.Exit(code=1)

    report = keyring_store.migrate_from_json(delete_json=not keep_json)
    if report["migrated"]:
        rprint(f"[green]Migrated to keyring:[/green] {', '.join(report['migrated'])}")
    if report["skipped"]:
        rprint(f"[yellow]Skipped:[/yellow] {', '.join(report['skipped'])}")
    if report["errors"]:
        rprint("[red]Errors:[/red]")
        for e in report["errors"]:
            rprint(f"  {e}")
        raise typer.Exit(code=2)
    if not (report["migrated"] or report["skipped"]):
        rprint("[bright_black]No JSON credentials to migrate.[/bright_black]")


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
    provider = cfg.active_provider
    model = cfg.active_model
    # Strip redundant provider prefix if the stored model already starts with it.
    prefix = f"{provider}/"
    if model.startswith(prefix):
        model = model[len(prefix) :]
    rprint(f"[bold]Active model:[/bold] {provider}/{model}")


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
    provider = provider.strip().lower()
    model = model.strip()

    # Validate against the provider registry (prefix-only; no network calls).
    from karna.providers import PROVIDERS

    if provider not in PROVIDERS:
        available = ", ".join(sorted(PROVIDERS))
        rprint(f"[red]Unknown provider '{provider}'. Available: {available}[/red]")
        raise typer.Exit(code=1)

    if not model:
        rprint("[red]Model name cannot be empty.[/red]")
        raise typer.Exit(code=1)

    cfg = load_config()
    cfg.active_provider = provider
    cfg.active_model = model
    save_config(cfg)
    rprint(f"[green]Active model set to [bold]{provider}/{model}[/bold][/green]")


# --------------------------------------------------------------------------- #
#  Thinking-mode command
# --------------------------------------------------------------------------- #


@app.command("think")
def think(
    mode: str = typer.Argument(
        None,
        help="on | off | auto. Omit to show current state.",
    ),
) -> None:
    """Toggle Nellie's extended-thinking mode.

    ``on``   — always request a reasoning budget from the provider.
    ``off``  — never request reasoning.
    ``auto`` — let Nellie pick based on the active model name (default).

    Called with no argument, prints the current setting and how it
    resolves for the currently active model.
    """
    cfg = load_config()

    if mode is None:
        # Query mode — display current config + auto-resolution.
        setting = cfg.thinking_enabled
        if setting is None:
            label = "auto"
        elif setting:
            label = "on"
        else:
            label = "off"
        resolved = effective_thinking(cfg.active_model, cfg)
        rprint(f"[bold]Thinking mode:[/bold] {label}")
        rprint(f"[bold]Active model:[/bold]  {cfg.active_provider}/{cfg.active_model}")
        rprint(f"[bold]Resolved:[/bold]      {'on' if resolved else 'off'}")
        rprint(f"[bold]Budget:[/bold]        {cfg.thinking_budget_tokens} tokens")
        return

    normalized = mode.strip().lower()
    if normalized in ("on", "true", "1", "yes"):
        cfg.thinking_enabled = True
        label = "on"
    elif normalized in ("off", "false", "0", "no"):
        cfg.thinking_enabled = False
        label = "off"
    elif normalized in ("auto", "default", "unset", "none"):
        cfg.thinking_enabled = None
        label = "auto"
    else:
        rprint(f"[red]Invalid mode '{mode}'. Use: on | off | auto[/red]")
        raise typer.Exit(code=1)

    save_config(cfg)
    resolved = effective_thinking(cfg.active_model, cfg)
    rprint(f"[green]Thinking mode set to [bold]{label}[/bold][/green]")
    rprint(f"[bright_black]Resolved for {cfg.active_model}: {'on' if resolved else 'off'}[/bright_black]")


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
        if key == "memory":
            # Memory section rendered separately by `config memory`.
            table.add_row(key, "(use `nellie config memory` for details)")
        else:
            table.add_row(key, str(value))
    rprint(table)


@config_app.command("memory")
def config_memory() -> None:
    """Show current memory configuration."""
    from karna.config import _BUILTIN_MEMORY_TYPES

    cfg = load_config()
    mem = cfg.memory

    table = Table(title="Memory Configuration")
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("directory", mem.directory)
    types_display = ", ".join(mem.types)
    table.add_row("types", types_display)
    table.add_row("auto_extract", str(mem.auto_extract))
    table.add_row("rate_limit_turns", str(mem.rate_limit_turns))
    table.add_row("dedup_threshold", str(mem.dedup_threshold))
    table.add_row("index_file", mem.index_file)

    # Show which types are custom
    custom = [t for t in mem.types if t not in _BUILTIN_MEMORY_TYPES]
    if custom:
        table.add_row("custom_types", ", ".join(custom))

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
#  Recipe runner
# --------------------------------------------------------------------------- #


@app.command("run")
def run_recipe_cli(
    recipe: str = typer.Option(..., "--recipe", "-r", help="Path to a recipe YAML file"),
    param: list[str] = typer.Option(
        [],
        "--param",
        "-p",
        help="key=value recipe parameter (repeatable)",
    ),
    workspace: str = typer.Option(
        "",
        "--workspace",
        "-w",
        help="Directory the recipe's tools should scope to (bash cwd + write/edit allowed_roots)",
    ),
) -> None:
    """Execute a recipe YAML end-to-end.

    A recipe bundles instructions + parameters + tool allowlist + model
    pin into one reusable spec. Goose-parity surface.
    """
    from karna.recipes import load_recipe, run_recipe

    params: dict[str, str] = {}
    for kv in param:
        if "=" not in kv:
            rprint(f"[red]--param must be key=value (got: {kv!r})[/red]")
            raise typer.Exit(code=1)
        k, v = kv.split("=", 1)
        params[k.strip()] = v

    try:
        rec = load_recipe(recipe)
    except Exception as exc:
        rprint(f"[red]Failed to load recipe: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    result = asyncio.run(run_recipe(rec, params, workspace=workspace or None))
    halt = result["halt"]
    rprint(f"[bright_black]halt: {halt}[/bright_black]")
    if halt == "done":
        rprint(result["text"])
    else:
        if result.get("text"):
            rprint(result["text"])
        if result["errors"]:
            rprint(f"[red]errors: {'; '.join(result['errors'][-3:])}[/red]")
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------- #
#  REST server command
# --------------------------------------------------------------------------- #


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address"),
    port: int = typer.Option(3030, "--port", help="Bind port"),
) -> None:
    """Run Nellie as a REST + SSE server over HTTP.

    Exposes session-scoped agent turns via ``/v1/sessions`` and live
    events via ``/v1/sessions/{id}/events`` (Server-Sent Events).
    Requires the ``rest`` optional extra: ``pip install 'karna[rest]'``.
    """
    from karna.rest_server import serve as _serve

    _serve(host=host, port=port)


@app.command("web")
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address"),
    port: int = typer.Option(3030, "--port", help="Bind port"),
) -> None:
    """Launch the web UI -- opens browser automatically.

    Serves a browser-based interface with session management, live
    transcript streaming, recipe browsing, and memory management.
    Requires the ``webui`` optional extra: ``pip install 'karna[webui]'``.
    """
    from karna.web.app import serve_web

    serve_web(host=host, port=port)


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

    from karna.sessions.cost import CostTracker
    from karna.sessions.db import SessionDB

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


@mcp_app.command("serve")
def mcp_serve() -> None:
    """Run Nellie as an MCP server over stdio.

    Exposes a single ``nellie_agent`` tool that spawns a full agent-loop
    turn and returns its reply. Connect from any MCP client with a
    server config like::

        {"command": "nellie", "args": ["mcp", "serve"]}
    """
    from karna.mcp_server import serve

    serve()


@mcp_app.command("serve-memory")
def mcp_serve_memory() -> None:
    """Start the Memory MCP server (JSON-RPC over stdio).

    Exposes four tools -- ``memory_list``, ``memory_get``,
    ``memory_save``, and ``memory_delete`` -- so external MCP clients
    can read and write Nellie's persistent memory.

    Connect from any MCP client with a server config like::

        {"command": "nellie", "args": ["mcp", "serve-memory"]}
    """
    from karna.mcp_server.memory_server import run_memory_server

    run_memory_server()


@acp_app.command("serve")
def acp_serve() -> None:
    """Run Nellie as an ACP (Agent Client Protocol) server over stdio.

    ACP is JSON-RPC 2.0 over stdio for agent↔agent communication — a peer
    agent opens a session, streams user prompts, and receives ``session/update``
    notifications from us. Connect with a client config like::

        {"command": "nellie", "args": ["acp", "serve"]}
    """
    from karna.acp_server import serve

    serve()


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

# Minimal starter template when --minimal or no project detection can
# produce anything richer.  This is the "onboarding-friendly" template
# from the E8 spec.
_STARTER_TEMPLATE = """\
# Project Instructions for Nellie

## Conventions
<!-- Add your team's coding conventions here -->

## Tools & Stack
<!-- What technologies does this project use? -->

## Rules
<!-- Any rules Nellie should follow in this project -->
"""

# Starter config.toml including documented [memory] section
_STARTER_CONFIG_TOML = """\
# Nellie configuration — generated by `nellie init`
# See https://github.com/Viraj0518/Karna-GenAI-CLI for docs.

active_model = "openrouter/auto"
active_provider = "openrouter"
system_prompt = "You are Nellie, Karna's AI assistant."
max_tokens = 4096
temperature = 0.7
safe_mode = false
thinking_budget_tokens = 10000

[memory]
# Root directory for memory files (supports ~ expansion)
directory = "~/.karna/memory"
# Allowed memory types: built-in are user, feedback, project, reference.
# Add custom types like "runbook" or "sop" here.
types = ["user", "feedback", "project", "reference"]
# Automatically extract memories from conversation turns
auto_extract = true
# Minimum turns between automatic memory saves (0 = every turn)
rate_limit_turns = 5
# Word-overlap ratio above which a candidate is considered a duplicate (0.0-1.0)
dedup_threshold = 0.60
# Name of the index file inside the memory directory
index_file = "MEMORY.md"
"""


@app.command()
def init(
    provider: str = typer.Option(None, help="Default provider"),
    model: str = typer.Option(None, help="Default model"),
    minimal: bool = typer.Option(False, "--minimal", "-m", help="Use a minimal starter template"),
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
        rprint(
            "[bright_black]Nellie will read these automatically. "
            "Creating KARNA.md for additional instructions.[/bright_black]"
        )

    # Generate & write KARNA.md
    karna_md = cwd / "KARNA.md"
    if karna_md.exists():
        rprint("[yellow]KARNA.md already exists. Skipping.[/yellow]")
    else:
        if minimal:
            template = _STARTER_TEMPLATE
        else:
            project_type = detect_project_type(cwd)
            template = generate_karna_md_for_path(cwd, project_type, provider, model)
        karna_md.write_text(template)
        rprint(f"[green]Created KARNA.md ({len(template)} bytes)[/green]")

    # Create .karna/ project dir
    project_dir = cwd / ".karna"
    project_dir.mkdir(exist_ok=True)
    (project_dir / ".gitignore").write_text("*\n")

    # Ensure a global config with memory defaults exists
    from karna.config import CONFIG_PATH, KARNA_DIR

    if not CONFIG_PATH.exists():
        KARNA_DIR.mkdir(parents=True, exist_ok=True)
        starter_config = _STARTER_CONFIG_TOML
        CONFIG_PATH.write_text(starter_config)
        rprint(f"[green]Created starter config at {CONFIG_PATH}[/green]")

    rprint("\n[green][OK] Project initialized. Run `nellie` to start.[/green]")


# --------------------------------------------------------------------------- #
#  Cron commands
# --------------------------------------------------------------------------- #


@cron_app.callback(invoke_without_command=True)
def cron_root(ctx: typer.Context) -> None:
    """List scheduled jobs (default action)."""
    if ctx.invoked_subcommand is not None:
        return
    cron_list()


@cron_app.command("add")
def cron_add(
    name: str = typer.Option(..., "--name", help="Human-readable job name"),
    schedule: str = typer.Option(..., "--schedule", help="Cron expression or @daily/@hourly/..."),
    prompt: str = typer.Option(..., "--prompt", help="Prompt to feed the agent"),
    model: str = typer.Option("", "--model", help="Optional provider:model override"),
) -> None:
    """Register a new scheduled job."""
    from karna.cron.expression import CronParseError, parse_expression
    from karna.cron.store import CronStore

    try:
        parse_expression(schedule)
    except CronParseError as exc:
        rprint(f"[red]Invalid schedule: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    store = CronStore()
    job = store.add_job(name=name, schedule=schedule, prompt=prompt, model=model)
    rprint(f"[green]Added cron job [bold]{job.id}[/bold] ({job.name}) — {job.schedule}[/green]")


@cron_app.command("list")
def cron_list() -> None:
    """List every configured cron job."""
    from karna.cron.runner import summarize_job
    from karna.cron.store import CronStore

    jobs = CronStore().list_jobs()
    if not jobs:
        rprint("[bright_black]No cron jobs configured.[/bright_black]")
        return

    table = Table(title="Cron Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="white")
    table.add_column("Schedule", style="green")
    table.add_column("Enabled", style="yellow")
    table.add_column("Last Run", style="bright_black")
    table.add_column("Next Fire", style="bright_black")
    for job in jobs:
        info = summarize_job(job)
        table.add_row(
            job.id,
            job.name,
            job.schedule,
            "yes" if job.enabled else "no",
            (info.get("last_run_at") or "")[:19].replace("T", " "),
            (info.get("next_fire_at") or "")[:19].replace("T", " "),
        )
    rprint(table)


@cron_app.command("remove")
def cron_remove(job_id: str = typer.Argument(..., help="Cron job id (or prefix)")) -> None:
    """Delete a cron job by id."""
    from karna.cron.store import CronStore

    if CronStore().remove_job(job_id):
        rprint(f"[green]Removed cron job {job_id}[/green]")
    else:
        rprint(f"[red]No cron job matches {job_id}[/red]")
        raise typer.Exit(code=1)


@cron_app.command("enable")
def cron_enable(job_id: str = typer.Argument(..., help="Cron job id (or prefix)")) -> None:
    """Enable a cron job."""
    from karna.cron.store import CronStore

    if CronStore().set_enabled(job_id, True):
        rprint(f"[green]Enabled {job_id}[/green]")
    else:
        rprint(f"[red]No cron job matches {job_id}[/red]")
        raise typer.Exit(code=1)


@cron_app.command("disable")
def cron_disable(job_id: str = typer.Argument(..., help="Cron job id (or prefix)")) -> None:
    """Disable a cron job."""
    from karna.cron.store import CronStore

    if CronStore().set_enabled(job_id, False):
        rprint(f"[green]Disabled {job_id}[/green]")
    else:
        rprint(f"[red]No cron job matches {job_id}[/red]")
        raise typer.Exit(code=1)


@cron_app.command("tick")
def cron_tick() -> None:
    """Run any due cron jobs once (for OS-level cron wrapping)."""
    from karna.cron.runner import scan_and_fire

    fired = asyncio.run(scan_and_fire())
    if not fired:
        rprint("[bright_black]No jobs due.[/bright_black]")
        return
    for job, text in fired:
        snippet = (text or "")[:120].replace("\n", " ")
        rprint(f"[green]fired[/green] {job.id} ({job.name}) -> {snippet}")


@cron_app.command("run")
def cron_run(job_id: str = typer.Argument(..., help="Cron job id (or prefix) to run immediately")) -> None:
    """Run a single cron job immediately, regardless of schedule."""
    from karna.cron.runner import run_job
    from karna.cron.store import CronStore

    store = CronStore()
    job = store.get_job(job_id)
    if job is None:
        rprint(f"[red]No cron job matches {job_id}[/red]")
        raise typer.Exit(code=1)

    rprint(f"[bright_black]Running cron job {job.id} ({job.name})...[/bright_black]")
    text = asyncio.run(run_job(job, store=store))
    snippet = (text or "")[:500].replace("\n", " ")
    rprint(f"[green]done[/green] {job.id} ({job.name}) -> {snippet}")


@cron_app.command("daemon")
def cron_daemon(
    poll: int = typer.Option(60, "--poll", "-p", help="Polling interval in seconds"),
) -> None:
    """Start a long-running daemon that checks for due jobs periodically."""
    from karna.cron.daemon import run_daemon

    rprint(f"[bright_black]Starting cron daemon (poll={poll}s). Press Ctrl+C to stop.[/bright_black]")
    try:
        asyncio.run(run_daemon(poll_seconds=poll))
    except KeyboardInterrupt:
        rprint("\n[bright_black]Cron daemon stopped.[/bright_black]")


@cron_app.command("show")
def cron_show(job_id: str = typer.Argument(..., help="Cron job id (or prefix)")) -> None:
    """Show full details for one cron job."""
    from karna.cron.runner import summarize_job
    from karna.cron.store import CronStore

    job = CronStore().get_job(job_id)
    if job is None:
        rprint(f"[red]No cron job matches {job_id}[/red]")
        raise typer.Exit(code=1)
    info = summarize_job(job)
    rprint(f"[bold]{job.id}[/bold] — {job.name}")
    rprint(f"  Schedule : {job.schedule}")
    rprint(f"  Enabled  : {job.enabled}")
    rprint(f"  Model    : {job.model or '(default)'}")
    rprint(f"  Prompt   : {job.prompt}")
    rprint(f"  Last run : {job.last_run_at or '(never)'}")
    rprint(f"  Next fire: {info.get('next_fire_at') or '(unknown)'}")
    if job.last_result_snippet:
        rprint(f"  Last out : {job.last_result_snippet}")


# --------------------------------------------------------------------------- #
#  Fork / replay commands
# --------------------------------------------------------------------------- #


@app.command()
def fork(
    session_id: str = typer.Argument(..., help="Source session id to fork"),
    name: str = typer.Option(None, "--name", help="Optional new session name/summary"),
) -> None:
    """Duplicate a session (messages + metadata) into a new session."""
    from karna.sessions.db import SessionDB

    db = SessionDB()
    try:
        new_id = db.fork_session(session_id, new_name=name)
    except KeyError:
        rprint(f"[red]Source session not found: {session_id}[/red]")
        db.close()
        raise typer.Exit(code=1) from None
    db.close()
    rprint(f"[green]Forked {session_id} -> [bold]{new_id}[/bold][/green]")


@app.command()
def replay(
    session_id: str = typer.Argument(..., help="Session id to replay"),
) -> None:
    """Re-feed user messages through the active agent loop and print responses."""
    from karna.sessions.db import SessionDB

    db = SessionDB()
    conv = db.resume_session(session_id)
    db.close()
    if conv is None:
        rprint(f"[red]Session not found: {session_id}[/red]")
        raise typer.Exit(code=1)

    user_msgs = [m for m in conv.messages if m.role == "user"]
    if not user_msgs:
        rprint("[bright_black]No user messages to replay.[/bright_black]")
        return
    rprint(f"[bold]Replaying {len(user_msgs)} user prompt(s) from {session_id}[/bold]")
    for i, m in enumerate(user_msgs, 1):
        rprint(f"[cyan]#{i}[/cyan] {m.content[:200]}")


# --------------------------------------------------------------------------- #
#  Index / knowledge base commands
# --------------------------------------------------------------------------- #


@index_app.callback(invoke_without_command=True)
def index_root(
    ctx: typer.Context,
    path: str = typer.Argument(None, help="Path to index (file or directory)"),
    status: bool = typer.Option(False, "--status", help="Show what is indexed"),
    remove: str = typer.Option(None, "--remove", help="Remove a path from the index"),
) -> None:
    """Index files into the local knowledge base for RAG retrieval.

    Examples::

        nellie index .              # index current directory
        nellie index ~/docs/        # index a specific directory
        nellie index --status       # show what is indexed
        nellie index --remove ./old # remove from index
    """
    if ctx.invoked_subcommand is not None:
        return

    if status:
        _index_status()
        return

    if remove is not None:
        _index_remove(remove)
        return

    if path is not None:
        _index_path(path)
        return

    # No arguments — show help.
    rprint("[bright_black]Usage: nellie index <path> | --status | --remove <path>[/bright_black]")


def _index_path(path_str: str) -> None:
    """Index a file or directory."""
    from karna.rag.store import KnowledgeStore

    target = Path(path_str).resolve()
    if not target.exists():
        rprint(f"[red]Path not found: {target}[/red]")
        raise typer.Exit(code=1)

    store = KnowledgeStore()

    if target.is_file():
        rprint(f"[bright_black]Indexing file: {target}[/bright_black]")
        count = asyncio.run(store.index_file(target))
        rprint(f"[green]Indexed {count} chunk(s) from {target.name}[/green]")
    else:
        rprint(f"[bright_black]Indexing directory: {target}[/bright_black]")
        count = asyncio.run(store.index_directory(target))
        stats = store.stats()
        rprint(f"[green]Indexed {count} chunk(s) across {stats['total_files']} file(s)[/green]")


def _index_status() -> None:
    """Show indexed files and stats."""
    import datetime

    from karna.rag.store import KnowledgeStore

    store = KnowledgeStore()
    indexed = store.list_indexed()
    stats = store.stats()

    if not indexed:
        rprint("[bright_black]No files indexed.[/bright_black]")
        rprint("[bright_black]Run `nellie index <path>` to add files to the knowledge base.[/bright_black]")
        return

    table = Table(title="Knowledge Base")
    table.add_column("File", style="cyan", max_width=60)
    table.add_column("Chunks", justify="right", style="white")
    table.add_column("Indexed At", style="bright_black")

    for f in indexed:
        ts = datetime.datetime.fromtimestamp(f.indexed_at).strftime("%Y-%m-%d %H:%M")
        # Shorten path for display.
        display_path = f.path
        home = str(Path.home())
        if display_path.startswith(home):
            display_path = "~" + display_path[len(home) :]
        table.add_row(display_path, str(f.chunk_count), ts)

    rprint(table)
    rprint(
        f"[bright_black]Total: {stats['total_files']} files, "
        f"{stats['total_chunks']} chunks, "
        f"{stats['store_size_bytes'] / 1024:.0f} KB on disk "
        f"(backend: {stats['backend']})[/bright_black]"
    )


def _index_remove(path_str: str) -> None:
    """Remove a path from the index."""
    from karna.rag.store import KnowledgeStore

    target = Path(path_str).resolve()
    store = KnowledgeStore()
    removed = asyncio.run(store.remove(target))

    if removed:
        rprint(f"[green]Removed {removed} chunk(s) for {target}[/green]")
    else:
        rprint(f"[yellow]No indexed entries found for {target}[/yellow]")


# --------------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------------- #


def main() -> None:
    """Entry point for the ``nellie`` console script."""
    app()


if __name__ == "__main__":
    main()
