"""Karna CLI — entry point exposed as ``nellie``.

Commands
--------
* ``nellie``              — enter the interactive REPL
* ``nellie auth login``   — authenticate with a provider
* ``nellie model``        — show / set the active model
* ``nellie config show``  — dump current configuration
"""

from __future__ import annotations

import asyncio

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
    help="Karna — personal-use AI agent harness.",
    no_args_is_help=False,
    invoke_without_command=True,
)

auth_app = typer.Typer(help="Authentication commands.")
model_app = typer.Typer(help="Model selection commands.", invoke_without_command=True)
config_app = typer.Typer(help="Configuration commands.")

app.add_typer(auth_app, name="auth")
app.add_typer(model_app, name="model")
app.add_typer(config_app, name="config")


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
    """Karna — personal-use AI agent harness. CLI binary: nellie."""
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
    """Dump the current Karna configuration."""
    cfg = load_config()
    table = Table(title="Karna Configuration")
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="green")
    for key, value in cfg.model_dump().items():
        table.add_row(key, str(value))
    rprint(table)


# --------------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------------- #

def main() -> None:
    """Entry point for the ``nellie`` console script."""
    app()


if __name__ == "__main__":
    main()
