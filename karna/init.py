"""Project initialisation helpers for ``nellie init``."""

from __future__ import annotations

from pathlib import Path


def detect_project_type(cwd: Path) -> str:
    """Detect project type from files present."""
    if (cwd / "pyproject.toml").exists() or (cwd / "setup.py").exists():
        return "python"
    if (cwd / "package.json").exists():
        return "node"
    if (cwd / "Cargo.toml").exists():
        return "rust"
    if (cwd / "go.mod").exists():
        return "go"
    return "generic"


def _read_python_meta(cwd: Path) -> dict[str, str]:
    """Best-effort read of project name and python version from pyproject.toml."""
    meta: dict[str, str] = {"name": cwd.name, "version": "3.x", "framework": "—"}
    toml_path = cwd / "pyproject.toml"
    if not toml_path.exists():
        return meta

    try:
        import sys

        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib  # type: ignore[no-redef]

        data = tomllib.loads(toml_path.read_text())
        project = data.get("project", {})
        meta["name"] = project.get("name", meta["name"])
        requires = project.get("requires-python", "")
        if requires:
            meta["version"] = requires
    except Exception:
        pass
    return meta


def generate_karna_md(project_type: str, provider: str | None = None, model: str | None = None) -> str:
    """Generate a KARNA.md template based on project type."""

    provider_line = f"- Provider: {provider}" if provider else "- Provider: (auto-detect)"
    model_line = f"- Model: {model}" if model else "- Model: (default)"

    templates: dict[str, str] = {
        "python": """\
# KARNA.md — Project Instructions

## Project
{name} — Python project

## Stack
- Python {version}
- Tests: pytest
- Linter: ruff

## Agent defaults
{provider_line}
{model_line}

## Conventions
- Run `pytest` before committing
- Use ruff for linting
- Follow existing code style

## What to work on
(Add your priorities here)
""",
        "node": """\
# KARNA.md — Project Instructions

## Project
{name} — Node.js project

## Stack
- Node.js / TypeScript
- Tests: vitest / jest

## Agent defaults
{provider_line}
{model_line}

## Conventions
- Run `npm test` before committing
- Follow existing code style

## What to work on
(Add your priorities here)
""",
        "rust": """\
# KARNA.md — Project Instructions

## Project
{name} — Rust project

## Stack
- Rust (Cargo)
- Tests: `cargo test`

## Agent defaults
{provider_line}
{model_line}

## Conventions
- Run `cargo test` before committing
- Use `cargo clippy` for linting
- Follow existing code style

## What to work on
(Add your priorities here)
""",
        "go": """\
# KARNA.md — Project Instructions

## Project
{name} — Go project

## Stack
- Go modules
- Tests: `go test ./...`

## Agent defaults
{provider_line}
{model_line}

## Conventions
- Run `go test ./...` before committing
- Use `go vet` and `golangci-lint`
- Follow existing code style

## What to work on
(Add your priorities here)
""",
        "generic": """\
# KARNA.md — Project Instructions

## Project
{name}

## Agent defaults
{provider_line}
{model_line}

## Conventions
- Follow existing code style

## What to work on
(Add your priorities here)
""",
    }

    tpl = templates.get(project_type, templates["generic"])

    # For python projects, try to extract richer metadata
    name = "my-project"
    version = "3.x"
    if project_type == "python":
        # We'd need cwd here but we don't have it; caller can pass it later.
        # For now use placeholders that are still useful.
        name = "my-project"
        version = "3.x"

    return tpl.format(
        name=name,
        version=version,
        provider_line=provider_line,
        model_line=model_line,
    )


def generate_karna_md_for_path(
    cwd: Path,
    project_type: str,
    provider: str | None = None,
    model: str | None = None,
) -> str:
    """Generate KARNA.md with real project metadata read from *cwd*."""
    provider_line = f"- Provider: {provider}" if provider else "- Provider: (auto-detect)"
    model_line = f"- Model: {model}" if model else "- Model: (default)"

    if project_type == "python":
        meta = _read_python_meta(cwd)
        name = meta["name"]
        version = meta["version"]
    else:
        name = cwd.name
        version = ""

    # Re-use the simple templates but with real values
    tpl_map: dict[str, str] = {
        "python": """\
# KARNA.md — Project Instructions

## Project
{name} — Python project

## Stack
- Python {version}
- Tests: pytest
- Linter: ruff

## Agent defaults
{provider_line}
{model_line}

## Conventions
- Run `pytest` before committing
- Use ruff for linting
- Follow existing code style

## What to work on
(Add your priorities here)
""",
        "node": """\
# KARNA.md — Project Instructions

## Project
{name} — Node.js project

## Stack
- Node.js / TypeScript
- Tests: vitest / jest

## Agent defaults
{provider_line}
{model_line}

## Conventions
- Run `npm test` before committing
- Follow existing code style

## What to work on
(Add your priorities here)
""",
        "rust": """\
# KARNA.md — Project Instructions

## Project
{name} — Rust project

## Stack
- Rust (Cargo)
- Tests: `cargo test`

## Agent defaults
{provider_line}
{model_line}

## Conventions
- Run `cargo test` before committing
- Use `cargo clippy` for linting
- Follow existing code style

## What to work on
(Add your priorities here)
""",
        "go": """\
# KARNA.md — Project Instructions

## Project
{name} — Go project

## Stack
- Go modules
- Tests: `go test ./...`

## Agent defaults
{provider_line}
{model_line}

## Conventions
- Run `go test ./...` before committing
- Use `go vet` and `golangci-lint`
- Follow existing code style

## What to work on
(Add your priorities here)
""",
        "generic": """\
# KARNA.md — Project Instructions

## Project
{name}

## Agent defaults
{provider_line}
{model_line}

## Conventions
- Follow existing code style

## What to work on
(Add your priorities here)
""",
    }

    tpl = tpl_map.get(project_type, tpl_map["generic"])
    return tpl.format(
        name=name,
        version=version,
        provider_line=provider_line,
        model_line=model_line,
    )
