"""Smoke tests for the nellie CLI."""

from typer.testing import CliRunner

from karna import __version__
from karna.cli import app

runner = CliRunner()


def test_help_exits_zero() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "nellie" in result.output.lower() or "karna" in result.output.lower()


def test_version_prints_correctly() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_config_show() -> None:
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "active_model" in result.output


def test_auth_login_stub() -> None:
    result = runner.invoke(app, ["auth", "login", "openrouter"])
    assert result.exit_code == 0
    assert "not yet implemented" in result.output.lower()
