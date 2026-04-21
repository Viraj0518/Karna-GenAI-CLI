# Nellie-vs-Goose Parity — Snapshot

> One-page dashboard. Full per-row matrix + reasoning in
> [`research/karna/NELLIE_VS_GOOSE_PARITY.md`](../research/karna/NELLIE_VS_GOOSE_PARITY.md)
> (alpha owns; do not edit from docs subagents).

**Status as of 2026-04-21: ~77% parity, up from ~62% 24 hours ago.**

## At a glance

| Status | Count | Subsystems |
|---|---|---|
| ✅ Shipped on `dev` (or merging now) | 20/26 | Agent core, provider trait, MCP client, built-in dev tools, sessions+SQLite, REST session manager, scheduler, CLI, HTTP server + SSE, ACP server, MCP server wrapping Nellie, permissions, context auto-compaction, OpenAPI, installers, recipes, unique-to-Nellie 27–30 |
| 🟡 In flight on a branch | 4/26 | Provider capability registry (#2+#13, beta B1), prompt injection (#22, beta B2), OS keyring (#12, beta B3), Memory MCP (#6, gamma G2 — on branch but see caveat below) |
| 🔴 Not started | 2/26 | computer_controller MCP (#5), WebSockets (#16), desktop app (#19), web UI (#20), telemetry (#26) |

## What shipped in the last 24h (alpha wave 1)

| # | Subsystem | Module | CLI | Commit |
|---|---|---|---|---|
| 8 | In-process session manager (REST) | `karna/rest_server/session_manager.py` | `nellie serve` | `ba95c96` |
| 15 | HTTP server — 10 endpoints + SSE + OpenAPI | `karna/rest_server/` | `nellie serve` | `ba95c96` |
| 9 | Recipe engine | `karna/recipes/` | `nellie run --recipe` | `6727e6b` |
| 17 | ACP server | `karna/acp_server/` | `nellie acp serve` | `7c37dac` |

Parity gain: rows 8, 9, 15, 17 moved from 🔴/🟡 to ✅ in one PR (`#48`).

## What gamma shipped on `claude/gamma-nellie-surfaces-20260420`

| # | Subsystem | Location | Commit | Status |
|---|---|---|---|---|
| 6 | Memory MCP server | `karna/mcp_server/memory_server.py`, 4 tools (`memory_list/get/save/delete`), `nellie mcp serve-memory`, 19 tests | `6533f80` | 🟡 **Accidentally removed** in follow-up `1e2f353` — needs recovery before merge |
| 25 | Installers | `install.sh` / `install.ps1` / `install.py` (stdlib), `packaging/homebrew/nellie.rb`, `docs/INSTALL.md` | `1e2f353` | ✅ Shipped |

## What beta shipped on `claude/beta-nellie-infra-20260420`

| # | Subsystem | Location | Commit | Status |
|---|---|---|---|---|
| 2+13 | Canonical model registry (1,359 models, OR + HF + direct) | `karna/providers/canonical_models.json` + loader | `b3bf6ca` | 🟡 Ready, pending PR #49 |
| 12 | OS keyring credential storage | `karna/auth/keyring_store.py` + `nellie auth migrate` | `f793569` | 🟡 Ready, pending PR #49 |
| 22 | Prompt-injection detection | `karna/security/prompt_injection.py` (17 patterns / 7 categories, NFKC-normalized) | `1a28558` | 🟡 Ready, pending PR #49 |

## What's left

- **alpha**: #5 computer_controller, #16 WebSockets (promote SSE → WS for the REST server), #26 telemetry (opt-in, zero by default)
- **beta**: close PR #49 (get B1/B2/B3 onto `dev`)
- **gamma**: recover `karna/mcp_server/memory_server.py` from `6533f80`, then #10 sub-recipes (G1), #19 Electron desktop, #20 web UI

## Exit criteria — "Nellie ≥ Goose"

Every row in the full matrix ✅ **and** the four unique-to-Nellie items retained (domain opinionation, multi-agent comms, auto-memory, skills). At that point: same surface, better domain fit, lower adoption friction for Karna.
