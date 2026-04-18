# Contributing to Karna / Nellie

Thanks for your interest in contributing. This document explains how to get a working dev environment, run tests, and ship a change.

## TL;DR

```bash
git clone https://github.com/<your-fork>/Karna-GenAI-CLI.git
cd Karna-GenAI-CLI
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
pre-commit install
python -m pytest
```

## Development environment

- **Python**: 3.10, 3.11, or 3.12. CI runs the full matrix on Ubuntu and Windows.
- **Virtualenv**: use any tool you like (`venv`, `uv`, `poetry shell`). The maintainers use plain `venv`.
- **Install the package in editable mode with dev extras**:

  ```bash
  python -m pip install -e ".[dev]"
  ```

- **Optional extras**:
  - `tokens` — enables `tiktoken`-based token counting.
  - `web` — enables `trafilatura`-based web fetching.

## Running tests

```bash
python -m pytest                               # all tests
python -m pytest tests/test_foo.py -x          # focused, fail fast
python -m pytest --cov=karna --cov-report=term # with coverage
```

Tests must pass on both Linux and Windows. If you use OS-specific paths or subprocess invocations, guard them with `sys.platform` and cover both branches.

## Lint, format, types

We standardize on `ruff` for both linting and formatting. Line length is 120.

```bash
ruff check karna/ tests/
ruff format karna/ tests/
ruff format --check karna/ tests/   # what CI runs
```

`pre-commit` runs these automatically on `git commit` once you've run `pre-commit install`.

## Coding conventions

- **Type hints**: required on all new public functions and class attributes. Use `from __future__ import annotations` where it helps avoid import cycles.
- **Async**: Karna is async-first. Prefer `async def` for anything touching I/O. Use `asyncio.gather` for concurrent fan-out. Do not block the event loop with sync I/O in hot paths.
- **Errors**: raise specific exceptions, never bare `except:`. Log with `logging`, not `print`.
- **Secrets**: never commit API keys, tokens, or `.env` files. `.env.example` is the template.
- **Security-sensitive code** (`karna/tools/bash.py`, `karna/security/guards.py`, `karna/auth/`, `karna/agents/safety.py`, etc.) requires a second reviewer.

## Commit style

Use [Conventional Commits](https://www.conventionalcommits.org/) where practical:

```
feat(agents): add planner subagent
fix(bash): escape shell args on Windows
docs(contributing): clarify dev setup
chore(deps): bump pydantic to 2.8
```

Keep the subject under 72 characters. Explain **why** in the body when the change isn't obvious.

## Pull requests

1. Fork and create a topic branch off `main`.
2. Make focused, reviewable commits. Avoid drive-by reformatting.
3. Add or update tests.
4. Update `CHANGELOG.md` under `## [Unreleased]`.
5. Open a PR using the template. Fill in the checklist.
6. CI must be green before a maintainer will review.

## Reporting security issues

See [SECURITY.md](SECURITY.md). Please do **not** open a public issue for a vulnerability.

## Code of conduct

All contributors are expected to follow the [Contributor Covenant](CODE_OF_CONDUCT.md).

## License + contribution terms

Nellie is **proprietary software** (see `LICENSE.md`). Use is restricted to the owner and organizations explicitly authorized in writing.

**Before opening a PR from outside Karna** — contact Viraj first. Contributions you submit are licensed back to Karna on the same proprietary terms; there is no implicit MIT / Apache / open-source grant. If that's a blocker for you, please don't submit a PR.

Internal Karna contributors: proceed normally.
