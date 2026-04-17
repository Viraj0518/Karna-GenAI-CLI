"""Tests for ``nellie init`` — project initialisation."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from karna.cli import app
from karna.init import detect_project_type, generate_karna_md

runner = CliRunner()


# --------------------------------------------------------------------------- #
#  detect_project_type
# --------------------------------------------------------------------------- #


def test_detect_python_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert detect_project_type(tmp_path) == "python"


def test_detect_python_setup_py(tmp_path: Path) -> None:
    (tmp_path / "setup.py").write_text("from setuptools import setup; setup()")
    assert detect_project_type(tmp_path) == "python"


def test_detect_node(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"x"}')
    assert detect_project_type(tmp_path) == "node"


def test_detect_rust(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n")
    assert detect_project_type(tmp_path) == "rust"


def test_detect_go(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/x\n")
    assert detect_project_type(tmp_path) == "go"


def test_detect_generic(tmp_path: Path) -> None:
    assert detect_project_type(tmp_path) == "generic"


# --------------------------------------------------------------------------- #
#  generate_karna_md
# --------------------------------------------------------------------------- #


def test_template_python_contains_pytest() -> None:
    md = generate_karna_md("python")
    assert "pytest" in md
    assert "KARNA.md" in md


def test_template_node_contains_npm() -> None:
    md = generate_karna_md("node")
    assert "npm test" in md


def test_template_rust_contains_cargo() -> None:
    md = generate_karna_md("rust")
    assert "cargo test" in md


def test_template_go_contains_go_test() -> None:
    md = generate_karna_md("go")
    assert "go test" in md


def test_template_generic_minimal() -> None:
    md = generate_karna_md("generic")
    assert "KARNA.md" in md
    assert "What to work on" in md


def test_template_includes_provider_and_model() -> None:
    md = generate_karna_md("python", provider="openrouter", model="llama-3.3")
    assert "openrouter" in md
    assert "llama-3.3" in md


# --------------------------------------------------------------------------- #
#  CLI integration: nellie init
# --------------------------------------------------------------------------- #


def test_init_creates_karna_md(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "KARNA.md").exists()
    assert "Created KARNA.md" in result.output


def test_init_skips_existing_karna_md(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    karna_md = tmp_path / "KARNA.md"
    karna_md.write_text("# existing\n")

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert "already exists" in result.output
    # Content should be untouched
    assert karna_md.read_text() == "# existing\n"


def test_init_creates_karna_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".karna").is_dir()
    assert (tmp_path / ".karna" / ".gitignore").read_text() == "*\n"


def test_init_detects_claude_md(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# Claude instructions\n")

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert "CLAUDE.md" in result.output
    assert "existing AI config" in result.output


def test_init_detects_cursorrules(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cursorrules").write_text("rules\n")

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert ".cursorrules" in result.output


def test_init_with_provider_and_model(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--provider", "anthropic", "--model", "claude-sonnet"])
    assert result.exit_code == 0, result.output
    content = (tmp_path / "KARNA.md").read_text()
    assert "anthropic" in content
    assert "claude-sonnet" in content
