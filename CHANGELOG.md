# Changelog

All notable changes to Nellie will be documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
