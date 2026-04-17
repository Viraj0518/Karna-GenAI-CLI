# Nellie Codebase Map -- Karna Engineering

> One-liner per `.py` file. Updated 2026-04-17.

## Core

```
karna/__init__.py                      — Package root, __version__, privacy notice
karna/cli.py                           — Typer CLI entry point (nellie binary), 14 commands + 6 sub-groups
karna/config.py                        — KarnaConfig Pydantic model, TOML load/save from ~/.karna/config.toml
karna/models.py                        — Message, Conversation, ToolCall, ToolResult, StreamEvent, Usage, Provider protocol, pricing table
karna/init.py                          — Project initialisation: detect type, generate KARNA.md templates
```

## Agents

```
karna/agents/__init__.py               — Re-exports agent_loop, agent_loop_sync, pre_tool_check
karna/agents/loop.py                   — Core agent loop: streaming + sync, parallel/sequential tool dispatch, retry, loop detection
karna/agents/safety.py                 — Pre-tool safety checks: dangerous commands, sensitive paths, SSRF guard
karna/agents/subagent.py               — SubAgent (independent agent with own conversation) + SubAgentManager registry
```

## Providers

```
karna/providers/__init__.py            — Provider registry: get_provider(), resolve_model(), lazy imports
karna/providers/base.py                — BaseProvider ABC: credential loading, retry with backoff, cost tracking, HTTPS enforcement
karna/providers/anthropic.py           — Anthropic Messages API: native format, SSE streaming, prompt caching, cache-adjusted cost
karna/providers/openrouter.py          — OpenRouter: primary backend, model aliases, OpenAI-compatible streaming
karna/providers/openai.py              — OpenAI chat completions: function calling, streaming with usage
karna/providers/azure.py               — Azure OpenAI: deployment-based routing, api-key header auth
karna/providers/local.py               — Local OpenAI-compatible servers (llama.cpp, vLLM, Ollama, LM Studio)
karna/providers/caching.py             — PromptCache: Anthropic cache_control markers, tool sorting for prefix stability
```

## Tools

```
karna/tools/__init__.py                — Tool registry: get_tool(), get_all_tools(), TOOLS dict
karna/tools/base.py                    — BaseTool ABC: name, description, parameters, sequential flag, format converters
karna/tools/bash.py                    — Shell execution: async subprocess, cwd tracking, output truncation, dangerous-command check
karna/tools/read.py                    — File reading: line numbers, offset+limit, binary/image detection, path safety
karna/tools/write.py                   — File writing: auto-mkdir, overwrite protection convention, path safety
karna/tools/edit.py                    — Exact string replacement: uniqueness check, replace_all, new-file creation
karna/tools/grep.py                    — Regex search: ripgrep preferred, grep -rn fallback, glob filters, context lines
karna/tools/glob.py                    — File pattern matching: pathlib.glob, git-aware via git ls-files, mtime-sorted
karna/tools/web_search.py             — Web search: DuckDuckGo (default), Brave Search, SearXNG cascade, privacy-first
karna/tools/web_fetch.py              — URL fetching: SSRF guard, robots.txt, trafilatura extraction, content-type guard
karna/tools/clipboard.py              — Cross-platform clipboard: macOS/X11/Wayland/WSL via native utilities
karna/tools/image.py                  — Vision: base64 encoding, marker protocol for provider layer, format validation
karna/tools/git_ops.py                — Structured git ops: status/diff/log/add/commit/branch/stash/checkout with safety guards
karna/tools/monitor.py                — Background process streaming: each stdout line becomes a notification event
karna/tools/notebook.py               — Jupyter .ipynb: read/edit/add/execute/create, nbformat optional, exec() fallback
karna/tools/task.py                   — Subagent spawning: delegates to SubAgentManager, optional worktree isolation
karna/tools/mcp.py                    — MCP client: JSON-RPC over stdio, server lifecycle, tool discovery, MCPProxyTool wrapper
```

## Auth

```
karna/auth/__init__.py                 — Re-exports credential CRUD and CredentialPool
karna/auth/credentials.py              — JSON credential files in ~/.karna/credentials/, mode 0600, masked logging
karna/auth/pool.py                     — Multi-key pool: failover/round-robin/least-used, cooldown on 429, permanent removal on 401
```

## Context

```
karna/context/__init__.py              — Re-exports ContextManager
karna/context/manager.py               — Central context manager: project + git + env injection, token-budget truncation
karna/context/project.py               — Project context detection: KARNA.md, CLAUDE.md, .cursorrules, copilot-instructions, .karna/project.toml
karna/context/git.py                   — Git awareness: branch, status summary, recent commits, diff stat, parallel git commands
karna/context/environment.py           — Environment metadata: platform, shell, Python version, cwd, date
```

## Prompts

```
karna/prompts/__init__.py              — Re-exports build_system_prompt, adapt_for_model, generate_tool_docs
karna/prompts/system.py                — System prompt builder: template selection, tool docs, context injection, budget trimming, model adaptation
karna/prompts/tool_descriptions.py     — Auto-generate tool documentation sections from registry with usage guidance
```

## Sessions

```
karna/sessions/__init__.py             — Re-exports SessionDB and CostTracker
karna/sessions/db.py                   — SQLite FTS5 session database: messages, full-text search, resume, cost aggregation
karna/sessions/cost.py                 — CostTracker: per-session accumulation, cascading cost lookup, aggregate queries
```

## Memory

```
karna/memory/__init__.py               — Re-exports MemoryManager, MemoryEntry, MemoryType
karna/memory/types.py                  — Memory type taxonomy (user/feedback/project/reference) + MemoryEntry model
karna/memory/manager.py                — MemoryManager: CRUD, search, MEMORY.md index, context injection, staleness check
karna/memory/prompts.py                — MEMORY_SYSTEM_PROMPT: comprehensive instructions for auto-memory behavior
```

## Skills

```
karna/skills/__init__.py               — Re-exports Skill, SkillManager, parse_skill_file
karna/skills/loader.py                 — Skill loader: .md parsing with YAML frontmatter, SkillManager (load/match/install/create)
```

## Hooks

```
karna/hooks/__init__.py                — Stub (Phase 3)
karna/hooks/dispatcher.py              — HookDispatcher: register/dispatch lifecycle hooks, shell-command wrapper, config loading
karna/hooks/builtins.py                — Built-in hooks: cost warning, git dirty warning, auto-save memory (stub)
```

## Permissions

```
karna/permissions/__init__.py          — Re-exports PermissionLevel, PermissionManager, PermissionRule, PROFILES
karna/permissions/manager.py           — 3-tier permission system: ALLOW/ASK/DENY per tool, regex patterns, profiles, session grants
```

## Compaction

```
karna/compaction/__init__.py           — Stub (Phase 3)
karna/compaction/compactor.py          — Compactor: auto-summarize old messages, preserve tail, circuit breaker on failures
karna/compaction/prompts.py            — Summarization prompt templates for conversation compaction
```

## Tokens

```
karna/tokens/__init__.py               — Re-exports TokenCounter, count_tokens
karna/tokens/counter.py                — Model-aware token counter: tiktoken (optional) with o200k/cl100k selection, len//4 fallback
```

## Security

```
karna/security/__init__.py             — Re-exports all security guards and scrubbers
karna/security/guards.py               — Path traversal guard, secret scrubbing (8 patterns), SSRF guard, dangerous command detection
karna/security/scrub.py                — Memory-safe scrubbing: secrets + credential paths + base64 blob redaction
```

## TUI

```
karna/tui/__init__.py                  — Re-exports run_repl
karna/tui/repl.py                      — Main REPL loop: banner, input, slash commands, agent loop, streaming output, session persistence
karna/tui/output.py                    — OutputRenderer: Rich-based streaming (text/tool calls/results/errors/usage), spinner
karna/tui/input.py                     — Multiline input: prompt_toolkit preferred, plain input() fallback, backslash continuation
karna/tui/slash.py                     — 14 slash commands: help, model, clear, history, cost, exit, compact, tools, system, sessions, resume, paste, copy
karna/tui/banner.py                    — Startup banner: version, model, tool count in Rich panel with brand colours
karna/tui/themes.py                    — Colour theme: Karna brand palette (#3C73BD blue, #87CEEB sky-blue), Rich Theme object
```

## Plugin System

```
karna/plugins/__init__.py              — Public exports: KarnaContext, Plugin, PluginLoader, PluginManifestError
karna/plugins/loader.py                — Minimal plugin loader: discover ~/.karna/plugins/*, parse plugin.toml, import entry callable, activate with KarnaContext
```

## Removed Stubs

The previously listed `karna/backends/`, `karna/gateway/`, and `karna/server/`
stub packages have been deleted. Nellie is CLI + TUI only today; HTTP gateway,
remote daemon/server mode, and a backend connection-pooling abstraction are
tracked as future work in the root `README.md` Roadmap section.

## Prompt Templates

```
karna/prompts/templates/default.txt    — Default system prompt template (OpenAI/OpenRouter/Azure)
karna/prompts/templates/anthropic.txt  — Anthropic-specific system prompt template
karna/prompts/templates/weak_model.txt — Template for small/weak models with explicit tool reminders
```

## Tests

```
tests/__init__.py                      — Test package init
tests/test_agent_loop.py               — Agent loop iteration, tool dispatch, error recovery
tests/test_cli.py                      — CLI commands via typer.testing.CliRunner
tests/test_clipboard.py                — Clipboard tool platform detection and operations
tests/test_config.py                   — Config load/save, defaults, permission checks
tests/test_context.py                  — Context manager, project/git/env detection
tests/test_cost.py                     — Cost computation, tracker, aggregation queries
tests/test_error_recovery.py           — Malformed JSON repair, loop detection, empty response handling
tests/test_git_ops.py                  — Git tool safety guards and operations
tests/test_image.py                    — Image tool validation, marker parsing, content blocks
tests/test_init.py                     — Project type detection, KARNA.md generation
tests/test_mcp.py                      — MCP connection, JSON-RPC protocol, proxy tools
tests/test_prompts.py                  — System prompt building, model adaptations, tool docs
tests/test_providers.py                — Provider message serialization, usage extraction
tests/test_security.py                 — Path safety, secret scrubbing, SSRF, dangerous commands
tests/test_sessions.py                 — SessionDB CRUD, FTS5 search, session resume
tests/test_tools.py                    — BaseTool interface, tool registry, format converters
tests/test_tui.py                      — Output renderer event handling, slash commands
tests/test_web_tools.py                — Web search backends, fetch with SSRF, robots.txt
tests/dogfood_m27_docs.py              — Dogfood/integration test for documentation generation
```

## Project Files

```
pyproject.toml                         — Build config, dependencies, nellie entry point, ruff/pytest settings
install.sh                             — Installation script
LICENSE                                — MIT license
NOTICES.md                             — Attribution notices for ported code (cc-src, hermes-agent)
README.md                              — Project overview
.env.example                           — Example environment variables
.gitignore                             — Git ignore rules
.pre-commit-config.yaml                — Pre-commit hooks (ruff)
```
