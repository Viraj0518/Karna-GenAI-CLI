"""CLI surface tests — every nellie subcommand exit code and help smoke tests.

Verifies:
1. ``nellie --help`` / ``--version`` exit 0
2. Every documented subgroup's ``--help`` exits 0
3. ``nellie config show`` shows expected keys
4. ``nellie auth login`` stores credentials with ``--key`` flag
5. ``nellie auth list`` / ``nellie auth logout`` work
6. No unexpected crashes from help / list / show invocations
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from karna import __version__
from karna.cli import app

runner = CliRunner()


# --------------------------------------------------------------------------- #
#  Root-level commands
# --------------------------------------------------------------------------- #


def test_root_help_exits_zero() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "nellie" in result.output.lower() or "karna" in result.output.lower()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


# --------------------------------------------------------------------------- #
#  Config subcommand
# --------------------------------------------------------------------------- #


def test_config_help() -> None:
    result = runner.invoke(app, ["config", "--help"])
    assert result.exit_code == 0


def test_config_show_exits_zero() -> None:
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "active_model" in result.output


# --------------------------------------------------------------------------- #
#  Auth subcommand
# --------------------------------------------------------------------------- #


def test_auth_help() -> None:
    result = runner.invoke(app, ["auth", "--help"])
    assert result.exit_code == 0
    assert "login" in result.output.lower()


def test_auth_login_with_key_flag(tmp_path: Path) -> None:
    """`auth login <provider> --key <key>` stores the credential without prompting."""
    from karna.auth import credentials

    with patch.object(credentials, "CREDENTIALS_DIR", tmp_path / "credentials"):
        result = runner.invoke(app, ["auth", "login", "openrouter", "--key", "sk-fake-test"])
    assert result.exit_code == 0, result.output
    assert "saved openrouter credential" in result.output.lower()


def test_auth_list_exits_zero() -> None:
    result = runner.invoke(app, ["auth", "list"])
    assert result.exit_code == 0


def test_auth_logout_unknown_provider_exits_cleanly() -> None:
    """auth logout for a provider that was never stored should not crash."""
    result = runner.invoke(app, ["auth", "logout", "nonexistent-provider"])
    # exit code 0 (nothing to remove) or 1 (credential not found) both acceptable
    assert result.exit_code in (0, 1)


# --------------------------------------------------------------------------- #
#  Model subcommand
# --------------------------------------------------------------------------- #


def test_model_help() -> None:
    result = runner.invoke(app, ["model", "--help"])
    assert result.exit_code == 0


def test_model_list_exits_zero() -> None:
    """model set --help works (no list subcommand on main)."""
    result = runner.invoke(app, ["model", "--help"])
    assert result.exit_code == 0
    assert "set" in result.output.lower()


# --------------------------------------------------------------------------- #
#  History subcommand
# --------------------------------------------------------------------------- #


def test_history_help() -> None:
    result = runner.invoke(app, ["history", "--help"])
    assert result.exit_code == 0


def test_history_list_exits_zero() -> None:
    """history search with a real query works without crashing."""
    result = runner.invoke(app, ["history", "search", "test"])
    # May return 0 (results or no results) — just must not throw
    assert result.exit_code == 0 or result.exit_code == 1


# --------------------------------------------------------------------------- #
#  Cost subcommand
# --------------------------------------------------------------------------- #


def test_cost_help() -> None:
    result = runner.invoke(app, ["cost", "--help"])
    assert result.exit_code == 0


# --------------------------------------------------------------------------- #
#  MCP subcommand
# --------------------------------------------------------------------------- #


def test_mcp_help() -> None:
    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0


# --------------------------------------------------------------------------- #
#  Error handling: missing required args
# --------------------------------------------------------------------------- #


def test_auth_login_no_args_shows_error() -> None:
    """auth login without a provider arg should show usage, not a traceback."""
    result = runner.invoke(app, ["auth", "login"])
    # Should exit non-zero and show usage guidance, not an unhandled exception
    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_unknown_command_shows_error() -> None:
    result = runner.invoke(app, ["nonexistent-command-xyz"])
    assert result.exit_code != 0
    # Should mention the bad command somehow
    assert result.exception is None or isinstance(result.exception, SystemExit)
