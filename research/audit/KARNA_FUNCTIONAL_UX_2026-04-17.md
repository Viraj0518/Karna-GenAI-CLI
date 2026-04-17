# Karna / Nellie CLI — Functional + UX Audit

**Date:** 2026-04-17  **Auditor:** alpha  **Time-box:** 30 min
**Env:** Windows 11, Python 3.11.9, Git Bash, fresh venv

## TL;DR

**Install + run + basic conversation: PARTIAL.** Install is clean, all CLI subcommands execute without tracebacks, REPL renders. But credential onboarding is a stub, `nellie init` crashes on Windows, and a few cosmetic bugs dent polish. For 4 hours of work, Gamma delivered surprisingly coherent scope.

## Evidence

- `pip install -e ".[dev]"` — clean, 0 warnings, 0 conflicts, ~18s.
- `nellie --help / --version / config show / model / auth / cost / mcp list / history` — all run.
- `pytest tests/` — **405 passed, 11 failed**. All 11 failures are Windows POSIX-perm tests + git path quirks, not real defects.
- REPL banner renders: "nellie v0.1.0 · openrouter:auto · 11 tools loaded".
- Fake key → agent loop catches httpx 401 as `Agent error: HTTPStatusError: 401...`. No traceback leaks.

## Dealbreakers (P0)

1. **`nellie init` crashes on default Windows terminal.** `rprint("\u2713 Project initialized...")` raises `UnicodeEncodeError` on cp1252. KARNA.md writes first so state is fine, but the user sees a traceback on their first real command. Fix: replace `\u2713` with `[OK]`. `karna/cli.py:515`.
2. **No working credential flow besides env var.** `nellie auth login openrouter` prints *"not yet implemented"* (`cli.py:98-103`), yet `karna/auth/credentials.py:save_credential()` already exists. CLI never calls it. Docs tell users to `export OPENROUTER_API_KEY=...` — fine on Unix, painful on Windows. Either ship `auth login` or delete the subcommand.

## High-priority issues (P1)

3. **`nellie model` prints `openrouter/openrouter/auto`** — double-prefix. Default config has `active_model="openrouter/auto"` and `active_provider="openrouter"`, then `cli.py:119` prints `{provider}/{model}`. Cosmetic but erodes first-run confidence.
4. **`nellie model set garbage:nothing` silently succeeds.** No validation against provider registry. Next REPL launch explodes at resolve time. Add a registry membership check in `model_set`.
5. **401 error not human-friendly.** The retry wrapper (`agents/loop.py:360-377`) deliberately skips 4xx. A 401 should say *"Invalid API key for openrouter. Set OPENROUTER_API_KEY or run `nellie auth login openrouter`."*
6. **Spurious security warning on every Windows command.** `[security] Credentials directory ... has mode 0o777`. NTFS doesn't meaningfully expose POSIX modes. Skip or rephrase when `os.name == 'nt'`.

## UX wins

1. **REPL banner + tool introspection.** On entry you see model and "11 tools loaded" — good signal before typing anything.
2. **`GETTING_STARTED.md` is genuinely useful.** Picks OpenRouter with reasoning, recommends Qwen3 Coder specifically, shows `/cost` and `/model` for mid-session switching. Rates 4/5.
3. **Scope is broad and coherent.** FTS5 history search, cost tracking, MCP add/list/test, `resume`, project-type-aware `init`, 14+ tools, streaming with retry/backoff, SQLite sessions, credential pooling scaffold. Architecture layered cleanly.

## Docs ratings

- **README.md — 3.5/5.** Correct install, clean feature table, accurate architecture tree. Missing first-call walkthrough + troubleshooting.
- **GETTING_STARTED.md — 4/5.** Opinionated, specific, onboarding-grade.
- **docs/ — 3/5.** `CODEBASE_MAP.md` useful. `DEVELOPER_GUIDE.md` (32KB) + `PHASE4_DESIGN.md` (31KB) + `DIFF_AUDIT.md` (25KB) look internal — classic docs-for-docs. `docs/generated/` duplicates material.
- **NOTICES.md — OK.** MIT attributions to Hermes Agent + OpenClaw both present.

## Ergonomics — one-liners

- **CLI shape:** logical, `gh`-like: `nellie auth|model|config|mcp|history|cost|resume|init`.
- **Error messages:** mixed — `model set` missing-colon caught, but garbage provider accepted; 401 leaks exception class name.
- **Config UX:** `~/.karna/config.toml`, 6 Pydantic-validated fields, hand-editable. Sane.
- **Credential UX:** env var works, `auth login` is a stub. Unacceptable gap for a v0.1.0 ship.

## Recommendation

Ship-worthy after ~2 hours more: fix #1 (5 min), wire `auth login` to existing `save_credential()` (45 min), fix #3 (5 min), fix #6 (5 min), friendly 401 (15 min). Everything else is P2 polish. Core engine, tool loop, provider abstraction, and session persistence are solid — do not rewrite.
