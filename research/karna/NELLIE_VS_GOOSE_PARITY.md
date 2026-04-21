# Nellie-vs-Goose parity plan — full scope

Goal: **every subsystem Goose has, in Python, shipped on dev before
next production window.** Written after the Instagram-reel detour +
Viraj's "full scope buddy" call.

## Full parity matrix (each row = a Goose subsystem)

| # | Subsystem | Goose form | Nellie today | Owner | Status |
|---|---|---|---|---|---|
| 1 | Agent core | `Agent::reply()` in Rust | `karna/agents/loop.py` | alpha | ✅ shipped |
| 2 | Provider trait | abstract trait + 1700-model registry | `karna/providers/` (7 providers, string model ids, no capability registry) | beta | 🟡 in flight (B1 — registry) |
| 3 | ExtensionManager + MCP client | crate-level | `karna/tools/mcp.py` | — | ✅ shipped |
| 4 | Built-in MCP servers — developer | file I/O, shell, edit | already 19 tools | — | ✅ shipped (via nellie's own tools) |
| 5 | Built-in MCP servers — computer_controller | xcap screen capture + input automation | ❌ nothing | alpha | 🔴 todo |
| 6 | Built-in MCP servers — memory | persistent KV | `karna/mcp_server/memory_server.py` (list/get/save/delete, 19 tests) + `nellie mcp serve-memory` | gamma | ✅ shipped (restored in 7a3c6e2 after 1e2f353 accidental revert; PR #50) |
| 7 | SessionManager + SQLite | `sessions.db`, durable | `karna/sessions/` (SQLite + FTS5, multi-session CLI) | — | ✅ shipped |
| 8 | In-process session manager (for REST) | concurrent sessions in `goosed` | `karna/rest_server/session_manager.py` | alpha | ✅ shipped (PR #48) |
| 9 | Recipe Engine | YAML + MiniJinja | ❌ Skills are triggers, not workflows | alpha | 🔴 todo (next) |
| 10 | Sub-recipes | recipes invoking recipes | `karna/recipes/sub.py` — 3-level nesting, Jinja2 param flow, 17 tests | gamma | ✅ shipped (PR #50 commit 4aee3a4) |
| 11 | Scheduler | tokio-cron | `karna/cron/` | — | ✅ shipped |
| 12 | Configuration + keyring | OS keyring | 🟡 JSON files with 0600 | beta | 🟡 in flight (B3) |
| 13 | Canonical model registry | 1700 LLMs × capabilities | ❌ | beta | 🟡 in flight (B1, scoped 200 → now 1000+) |
| 14 | CLI | `goose session`/`run`/`configure`/`mcp` | `nellie` + 12 subcommands | — | ✅ shipped (+`serve` in PR #48) |
| 15 | HTTP server (goosed) | REST + SSE, 103 endpoints, OpenAPI | `karna/rest_server/` (10 endpoints, SSE, OpenAPI) | alpha | ✅ shipped (PR #48) |
| 16 | WebSockets | real-time updates | `karna/rest_server/app.py::session_ws` at `ws://host:port/v1/ws/sessions/{id}` — same event vocabulary as SSE + ping/cancel/message control frames, 5 protocol tests | alpha | ✅ shipped |
| 17 | ACP server (Agent Client Protocol) | JSON-RPC stdio | `karna/acp_server/` (session/new/list/prompt/cancel/close + session/update stream) | alpha | ✅ shipped (PR #48) |
| 18 | MCP server wrapping Nellie | — | `karna/mcp_server/` | — | ✅ shipped (unique to Nellie) |
| 19 | Desktop app | Electron + React | ❌ | gamma | 🔴 todo (was "web UI MVP" — scope now includes Electron) |
| 20 | Web UI | served by goosed | `karna/web/` — FastAPI + Jinja2 + htmx; 4 pages (sessions, live SSE transcript, recipes, memory); `nellie web`; 17 tests | gamma | ✅ shipped (PR #50 commit 41a1f49) |
| 21 | Permission modes | ask / deny / approve | 3-tier ALLOW/ASK/DENY per tool | — | ✅ shipped |
| 22 | Prompt injection detection | built-in | ❌ (path/SSRF/secret guards only) | beta | 🟡 in flight (B2) |
| 23 | Context auto-compaction | at 80% | `karna/compaction/` | — | ✅ shipped |
| 24 | OpenAPI spec generation | for client codegen | auto via FastAPI | alpha | ✅ shipped (PR #48) |
| 25 | Installers | signed pkg / MSI / deb | `install.sh` + `install.ps1` + `install.py` (stdlib cross-platform) + Homebrew formula + twine-verified release.yml | gamma | ✅ shipped |
| 26 | Telemetry | opt-in usage metrics | `karna/telemetry.py` — `KARNA_TELEMETRY=1` or `[tui].telemetry_enabled` opt-in, append-only `~/.karna/telemetry.jsonl`, no network egress, tokens+duration only (never message content) | alpha | ✅ shipped |

## Unique-to-Nellie (keep)

27. **Domain opinionation** — KARNA-*.md templates, Karna.md
    hierarchy, 30-persona library
28. **Multi-agent file-based comms** — `karna/comms/`
29. **Auto-memory extraction** — pattern-detection via regex, typed
30. **Skills system** — markdown-based triggers

## What's actually left (by subsystem)

- alpha: lane closed (#5 computer_controller shipped by beta, #16 WebSockets shipped, #26 telemetry shipped)
- beta: #2+#13 canonical registry (expand scope to 1000+ models), #22 prompt-injection, #12 keyring, CI/test harden
- gamma: #19 Electron desktop (confirmed: web UI done, Electron wrapper on gamma next)

## Exit criteria — "Nellie > Goose"

Every row above is ✅, and Nellie retains all unique-to-Nellie items.
At that point: same feature surface, better domain fit, lower
adoption friction for Karna.

## Cadence

- Alpha pushes feature PRs on `claude/alpha-nellie-*-20260420*`
  branches, ~1 per subsystem
- Beta + gamma push on their own branches, PR against dev
- All reviews via dev; main only moves on release cut
- Comms protocol is the military-style affirm/complete discipline
  established 2026-04-20T23:30Z
