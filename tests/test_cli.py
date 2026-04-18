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


def test_auth_login_accepts_key_flag(tmp_path, monkeypatch) -> None:
    """`auth login <provider> --key <key>` stores the credential without prompting."""
    # Redirect the credentials dir to a temp path so we don't clobber real creds.
    from karna.auth import credentials

    monkeypatch.setattr(credentials, "CREDENTIALS_DIR", tmp_path / "credentials")

    result = runner.invoke(app, ["auth", "login", "openrouter", "--key", "sk-fake-test"])
    assert result.exit_code == 0, result.output
    assert "saved openrouter credential" in result.output.lower()
