# Changelog

All notable changes to Nellie will be documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.1.3] - 2026-04-21

Post-0.1.2 release (2026-04-18 cadence: 0.1.0 → 0.1.1 → 0.1.2 shipped UX polish and advanced features; 0.1.3 is the first release to bump the source-string `__version__` in sync with the git tag, closing the drift where earlier tags pointed at commits whose `pyproject.toml` still said `0.1.0`).

Three themes drove this cycle:

1. **TUI rewrite** — legacy custom-scroll REPL replaced by a `patch_stdout`-wrapped `Application(full_screen=False)` ported from Hermes (MIT). Native terminal scrollback / scrollbar / copy-paste now work. Paired with a verbatim port of Claude Code's TUI chrome (`karna.tui.cc_components`, 11 modules, 132 tests) as a library the REPL can progressively adopt.
2. **Prompt-layer faithfulness** — tool prompts now match Claude Code verbatim (`karna/prompts/cc_tool_prompts.py`). Fixes the "Nellie refuses web scraping" class of behaviour bug — the full upstream prompts flow through OpenAI + Anthropic tool schemas and the system-prompt tool-docs section.
3. **Security + infra hardening** — notebook/database/browser/comms tool audits, PTY Linux fix, pytest hang root-caused + fixed, SSE web transcript, Playwright + visual-regression + CLI-surface + PTY test layers.

### Security

- **Notebook tool:** removed the unsandboxed in-process fallback. When neither `jupyter nbconvert` nor `papermill` is available on PATH, the tool refuses with a clear diagnostic instead of evaluating model-generated cell source in the host interpreter. Per-invocation nonce on temp filenames prevents concurrent-execution collisions. (`karna/tools/notebook.py`)
- **Database tool:** added a `params` field to the schema; all binds go through the wrapper's parameterised-execute path. DSN parsing rejects private/metadata hosts (loopback, RFC-1918, link-local, 169.254.169.254). Connect errors are routed through `scrub_secrets` so a failing connect no longer echoes plaintext passwords or bearer tokens. (`karna/tools/database.py`)
- **Browser tool:** registered a `page.route("**/*", ...)` handler that re-runs `is_safe_url` on every outgoing network request, including redirect targets and subresources. Closes both the DNS-rebinding hole and the redirect-chain hole at a single point. (`karna/tools/browser.py`)
- **Comms tool:** 1 MB cap on message body for both `send` and `reply` actions. (`karna/tools/comms.py`)
- Full audit at `research/karna/NEW_TOOLS_AUDIT_20260420.md`. Regression tests in `tests/test_database_tool_security.py`, `tests/test_comms_tool_security.py`, `tests/test_notebook_tool.py`.

### Added

- **Prompts: verbatim Claude Code tool prompts** — `karna/prompts/cc_tool_prompts.py` ports the full upstream CC prompts (bash, read, write, edit, grep, glob, web_fetch, web_search, notebook, task) from `/c/cc-src/src/tools/<Tool>/prompt.ts`. `BaseTool` gains a `cc_prompt` class attribute + `model_facing_description` property; API schemas (OpenAI + Anthropic) and the system-prompt tool-docs section now ship the rich CC text to the model. Fixes a behaviour bug where Nellie would refuse scraping tasks because `web_fetch`/`web_search` descriptions were thin one-liners. Templates also gained an explicit "you CAN browse the web in real time" paragraph. 8 regression assertions in `tests/test_cc_tool_prompts.py`.
- **TUI: `karna.tui.hermes_repl`** — patch_stdout-wrapped `Application(full_screen=False)` REPL port from Hermes. Native terminal scrollback, scrollbar, and copy/paste now Just Work; no more custom scroll buffer. Gated on `_USE_HERMES_REPL=True` in `karna/tui/__init__.py` for one-line rollback. Paired with `karna.tui.hermes_display` (spinner, tool preview, diff primitives).
- **TUI: `karna.tui.cc_components`** — 11-module faithful port of Claude Code's TUI chrome (`/c/cc-src/src/components/*`) to Rich renderables. 81 exported symbols, 132 tests green. Clusters: `chat`, `markdown`, `diffs`, `status`, `spinners`, `permissions`, `pickers`, `search`, `tasks`, `input`, `dialogs`. Library-only — REPL integration is a separate pass documented in `docs/CC_COMPONENT_LIBRARY.md`. Nellie brand skin applied (`#3C73BD`, `◆ nellie` assistant label, `✦/●/⎿` glyph vocabulary).
- `docs/CC_COMPONENT_LIBRARY.md` — upstream mapping, public surface, integration gaps, and CC→Rich semantic compromises for the component library.
- TUI scroll keybindings: PgUp / PgDn (page), Home / End (jump + toggle autoscroll lock), Ctrl-Up / Ctrl-Down (line). Output window is focusable so `Window.vertical_scroll` tracks properly. (`karna/tui/repl.py`)
- Esc-to-interrupt: cooperative soft interrupt distinct from Ctrl-C's hard cancel. Sets `state.interrupt_requested`; the agent loop winds down at the next event boundary. (`karna/tui/repl.py`)
- Autoscroll-to-bottom on new output unless the user has scrolled up (re-engaged with End).
- Bounded output buffer: 5000-line ring, oldest-first eviction, exact-overflow trim.
- Queued-message indicator in the status bar — `✉ N queued` visible while mid-stream steering messages are pending.
- Empty-reply warning: if a turn finishes with no TEXT_DELTA events and no error, a yellow note tells the user to check `/history` or rephrase.
- `docs/DEMO_WALKTHROUGH.md` — five-minute recording script for the production demo.
- `docs/SECURITY_HARDENING.md` — operator-facing summary of the security fixes above, with code links.
- `docs/CODEBASE_MAP.md`, `docs/DEVELOPER_GUIDE.md` refreshed with links to every source file and coverage of the dev subsystems (RAG, comms, cron, persona templates).

### Changed

- README: tool count 15/17 → 19 (matches the runtime tool registry; `karna/tools/voice.py` exists on disk but is deliberately not wired in). Dropped the stale BSD-3-Clause and telemetry-zero badges (the latter contradicted the body note about the one-time `sentence-transformers` Hugging Face download). `GETTING_STARTED.md` tool table now matches.

### Fixed

- **Tests: full pytest run no longer hangs.** `tests/test_background_bash.py` spawned real bash subprocesses via `run_in_background=True` but its autouse fixture only replaced the task-registry singleton — it never cancelled the in-flight `asyncio.Task` wrapping the subprocess. On the Windows Proactor loop the orphan held the subprocess transport open, deadlocking the next test that spawned anything. Fix: `TaskRegistry.shutdown()` cancels every RUNNING task's asyncio.Task and awaits its teardown; fixture upgraded to async and calls `shutdown()` before the sync reset. Whole file drops from a 300+s hang to ~10s. (`karna/tools/task_registry.py`, `tests/test_background_bash.py`)
- **PTY driver**: `_write` referenced `self._backend` which was never assigned on the instance — the ptyprocess branch never fired. Swap to module-level `_BACKEND` set by `_detect_backend()`. (`tools/tui_pty_driver.py`)
- Rotating placeholder text inside the REPL input buffer (removed per user direction — input line is now a bare chevron + cursor). (`karna/tui/repl.py`)
- Scrollbar arrows hidden; scrollbar itself is now interactive because the output window is focusable.

### Changed
- License: resolved conflict between `LICENSE` (MIT) and `license.md` (proprietary). `LICENSE.md` (proprietary usage restriction) is now the single authoritative terms file. `pyproject.toml` points at it. MIT `LICENSE` file deleted. Third-party MIT attributions preserved in `NOTICES.md`.

### Added
- Design token system: `karna/tui/design_tokens.py` with frozen palette, semantic roles, typography, spacing
- Icon set: `karna/tui/icons.py` with 25 Nerd Font glyphs + ASCII fallback (auto-detects terminal capability)
- `EventKind.THINKING_DELTA` stream event for reasoning-model output
- Tool calls render as 3-state widgets (pending / running / ok-err) with collapsible syntax-highlighted JSON args
- Error panels now pattern-match 401/403/429/timeout/SSL into actionable remediation hints
- Banner auto-detects project type (pyproject.toml / package.json / Cargo.toml / go.mod) + git status
- `/help` grouped into Session / Context / Utility panels with per-command icons
- Slash picker fuzzy unique-prefix matcher (`/m` → `/model`)
- Input prompt with chevron, placeholder, and first-run toolbar hint
- Visual audit: before/after SVG snapshots + side-by-side HTML diff in `research/ui-audit/`

### Changed
- `karna/tui/output.py` rewritten (218 → 602 LoC) with turn boundaries, sentence-boundary streaming (flicker fix on Windows)
- `karna/tui/themes.py` rewritten (44 → 202 LoC) — Rich Theme built from design tokens, all legacy names preserved
- Banner (`banner.py` 44 → 163), input (`input.py` 101 → 188), slash (`slash.py` 357 → 443) polished to match design system
- `meta` and `cost` tokens bumped from `text.tertiary` to `text.secondary` (WCAG AA: 10:1 contrast, was 3.3:1)

### Fixed
- Windows `cp1252` unicode crash in `nellie init` (swap `✓` to ASCII)
- Anthropic API key format error message clarified (`sk-ant-api03-` vs OAuth token)

## [0.1.0] - 2026-04-17

### Added

- Initial release — Phase 1-4 scaffold
- Providers: OpenRouter, OpenAI, Anthropic, Azure, Local, Vertex AI, AWS Bedrock
- Multi-credential failover across same-provider instances
- Tools: bash, read, write, edit, grep, glob, web_fetch, web_search, clipboard, image, git_ops, mcp, task, monitor
- TUI via Rich with streaming tool output, slash commands, multiline editing
- Skills system (agentskills.io compatible)
- Auto-memory with typed entries
- Lifecycle hooks (pre/post tool use, bash-error, user-prompt-submit)
- Auto-compaction on context overflow
- SQLite session persistence with FTS5 search
- Cost tracking
- Permission gate (ALLOW/ASK/DENY + remember)
- Security: path traversal guard, SSRF DNS-pin, secret scrubber, safe_mode bash default
