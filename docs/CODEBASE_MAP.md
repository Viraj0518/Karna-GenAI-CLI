# Nellie Codebase Map -- Karna Engineering

> One-liner per file. Each entry links to the source. Updated 2026-04-20.

## Core

- [karna/__init__.py](../karna/__init__.py) — Package root, `__version__`, privacy notice.
- [karna/cli.py](../karna/cli.py) — Typer CLI entry point (`nellie`): auth/model/config/history/resume/cost/mcp/cron/init commands + interactive REPL.
- [karna/config.py](../karna/config.py) — `KarnaConfig` Pydantic model, TOML load/save from `~/.karna/config.toml`.
- [karna/models.py](../karna/models.py) — `Message`, `Conversation`, `ToolCall`, `ToolResult`, `StreamEvent`, `Usage`, `Provider` protocol, `PRICING_TABLE`.
- [karna/init.py](../karna/init.py) — Project initialisation: detect type, generate KARNA.md templates.

## Agents

- [karna/agents/__init__.py](../karna/agents/__init__.py) — Re-exports `agent_loop`, `agent_loop_sync`, `pre_tool_check`.
- [karna/agents/loop.py](../karna/agents/loop.py) — Core agent loop: streaming + sync, parallel/sequential tool dispatch, retry, loop detection, auto-compaction trigger.
- [karna/agents/safety.py](../karna/agents/safety.py) — Pre-tool safety checks: dangerous commands, sensitive paths, SSRF guard.
- [karna/agents/subagent.py](../karna/agents/subagent.py) — `SubAgent` spawning, `SendMessage`, completion callbacks, worktree isolation + auto-cleanup, foreground/background modes.
- [karna/agents/autonomous.py](../karna/agents/autonomous.py) — `/loop` repeat-until-done autonomous agent cycle.
- [karna/agents/parallel.py](../karna/agents/parallel.py) — Parallel execution coordination for concurrent tool dispatch.
- [karna/agents/plan.py](../karna/agents/plan.py) — `/plan` read-only reasoning mode (think-first, no mutations).

## Providers

- [karna/providers/__init__.py](../karna/providers/__init__.py) — Provider registry: `get_provider()`, `resolve_model()`, lazy imports.
- [karna/providers/base.py](../karna/providers/base.py) — `BaseProvider` ABC: credential loading, retry with backoff, cost tracking, HTTPS enforcement.
- [karna/providers/anthropic.py](../karna/providers/anthropic.py) — Anthropic Messages API: native format, SSE streaming, prompt caching, cache-adjusted cost.
- [karna/providers/openrouter.py](../karna/providers/openrouter.py) — OpenRouter: primary backend, model aliases, OpenAI-compatible streaming.
- [karna/providers/openai.py](../karna/providers/openai.py) — OpenAI chat completions: function calling, streaming with usage.
- [karna/providers/azure.py](../karna/providers/azure.py) — Azure OpenAI: deployment-based routing, `api-key` header auth.
- [karna/providers/local.py](../karna/providers/local.py) — Local OpenAI-compatible servers (llama.cpp, vLLM, Ollama, LM Studio).
- [karna/providers/caching.py](../karna/providers/caching.py) — `PromptCache`: Anthropic `cache_control` markers, tool sorting for prefix stability.

## Tools

20 registered tools in [karna/tools/__init__.py](../karna/tools/__init__.py). Each is a `BaseTool` subclass with a `name`, `description`, `parameters` (JSON Schema), optional `sequential` flag, and an async `execute()` method.

- [karna/tools/__init__.py](../karna/tools/__init__.py) — Tool registry: `get_tool()`, `get_all_tools()`, `TOOLS` dict.
- [karna/tools/base.py](../karna/tools/base.py) — `BaseTool` ABC + format converters (`to_openai_tool`, `to_anthropic_tool`).
- [karna/tools/task_registry.py](../karna/tools/task_registry.py) — Shared `TaskRegistry` singleton for monitors, background bash, and subagents; drains pending notifications.
- [karna/tools/bash.py](../karna/tools/bash.py) — Shell execution: async subprocess, cwd tracking, output truncation, dangerous-command check.
- [karna/tools/read.py](../karna/tools/read.py) — File reading: line numbers, offset+limit, binary/image detection, path safety.
- [karna/tools/write.py](../karna/tools/write.py) — File writing: auto-mkdir, overwrite protection, path safety.
- [karna/tools/edit.py](../karna/tools/edit.py) — Exact string replacement: uniqueness check, replace_all, new-file creation.
- [karna/tools/grep.py](../karna/tools/grep.py) — Regex search: ripgrep preferred, `grep -rn` fallback, glob filters, context lines.
- [karna/tools/glob.py](../karna/tools/glob.py) — File pattern matching: `pathlib.glob`, git-aware via `git ls-files`, mtime-sorted.
- [karna/tools/web_search.py](../karna/tools/web_search.py) — Web search: DuckDuckGo (default), Brave Search, SearXNG cascade, privacy-first.
- [karna/tools/web_fetch.py](../karna/tools/web_fetch.py) — URL fetching: SSRF guard, robots.txt, trafilatura extraction, content-type guard.
- [karna/tools/clipboard.py](../karna/tools/clipboard.py) — Cross-platform clipboard: macOS/X11/Wayland/WSL via native utilities.
- [karna/tools/image.py](../karna/tools/image.py) — Vision: base64 encoding, marker protocol for provider layer, format validation.
- [karna/tools/git_ops.py](../karna/tools/git_ops.py) — Structured git ops: status/diff/log/add/commit/branch/stash/checkout with safety guards.
- [karna/tools/monitor.py](../karna/tools/monitor.py) — Background process streaming: each stdout line becomes a notification event via `TaskRegistry`.
- [karna/tools/notebook.py](../karna/tools/notebook.py) — Jupyter `.ipynb`: read/edit/add/create; runs cells only via `jupyter nbconvert` / `papermill` subprocesses — **refuses in-process evaluation**.
- [karna/tools/document.py](../karna/tools/document.py) — Read `.docx`, `.xlsx`, `.pdf`, `.pptx` via python-docx/openpyxl/pypdf; macro extensions rejected.
- [karna/tools/task.py](../karna/tools/task.py) — Spawn subagents (`create`/`send_message`/`stop`) with optional git worktree isolation.
- [karna/tools/database.py](../karna/tools/database.py) — SQLite / PostgreSQL / MySQL connector: read-only by default, **parameterised queries**, DSN SSRF guard, credential scrubbing on error.
- [karna/tools/browser.py](../karna/tools/browser.py) — Headless Chromium via Playwright: navigate/click/fill/screenshot; **per-request SSRF via `page.route()`** (closes DNS-rebinding + redirect holes).
- [karna/tools/comms.py](../karna/tools/comms.py) — Inter-agent inbox: send/check/read/reply; 1 MB body cap.
- [karna/tools/voice.py](../karna/tools/voice.py) — Voice TTS/STT prototype (pyttsx3 + SpeechRecognition). **Not currently registered** in `karna/tools/__init__.py`; file sits on disk awaiting a future registry pass.
- [karna/tools/mcp.py](../karna/tools/mcp.py) — MCP client: JSON-RPC over stdio, server lifecycle, tool discovery, `MCPProxyTool` wrapper.

## Auth

- [karna/auth/__init__.py](../karna/auth/__init__.py) — Re-exports credential CRUD and `CredentialPool`.
- [karna/auth/credentials.py](../karna/auth/credentials.py) — JSON credential files in `~/.karna/credentials/`, mode 0600, masked logging.
- [karna/auth/pool.py](../karna/auth/pool.py) — Multi-key pool: failover/round-robin/least-used, cooldown on 429, permanent removal on 401.

## Context

- [karna/context/__init__.py](../karna/context/__init__.py) — Re-exports `ContextManager`.
- [karna/context/manager.py](../karna/context/manager.py) — Central context manager: project + git + env injection, token-budget truncation.
- [karna/context/project.py](../karna/context/project.py) — Hierarchical KARNA.md / CLAUDE.md / `.cursorrules` merge (global + project-level).
- [karna/context/git.py](../karna/context/git.py) — Git awareness: branch, status summary, recent commits, diff stat, parallel git commands.
- [karna/context/environment.py](../karna/context/environment.py) — Environment metadata: platform, shell, Python version, cwd, date.
- [karna/context/references.py](../karna/context/references.py) — External reference tracking and context injection.

## Prompts

- [karna/prompts/__init__.py](../karna/prompts/__init__.py) — Re-exports `build_system_prompt`, `adapt_for_model`, `generate_tool_docs`.
- [karna/prompts/system.py](../karna/prompts/system.py) — System prompt builder: template selection, tool docs, context injection, budget trimming, model adaptation.
- [karna/prompts/tool_descriptions.py](../karna/prompts/tool_descriptions.py) — Auto-generate tool documentation sections from registry with usage guidance.
- [karna/prompts/templates/default.txt](../karna/prompts/templates/default.txt) — Default system prompt template (OpenAI/OpenRouter/Azure).
- [karna/prompts/templates/anthropic.txt](../karna/prompts/templates/anthropic.txt) — Anthropic-specific system prompt template.
- [karna/prompts/templates/weak_model.txt](../karna/prompts/templates/weak_model.txt) — Template for small/weak models with explicit tool reminders.

## Sessions

- [karna/sessions/__init__.py](../karna/sessions/__init__.py) — Re-exports `SessionDB` and `CostTracker`.
- [karna/sessions/db.py](../karna/sessions/db.py) — SQLite FTS5 session database: messages, full-text search, resume, cost aggregation.
- [karna/sessions/cost.py](../karna/sessions/cost.py) — `CostTracker`: per-session accumulation, cascading cost lookup, aggregate queries.

## Memory

- [karna/memory/__init__.py](../karna/memory/__init__.py) — Re-exports `MemoryManager`, `MemoryEntry`, `MemoryType`, `MemoryExtractor`.
- [karna/memory/types.py](../karna/memory/types.py) — Memory type taxonomy (user/feedback/project/reference) + `MemoryEntry` model.
- [karna/memory/manager.py](../karna/memory/manager.py) — CRUD, search, MEMORY.md index, context injection, staleness check.
- [karna/memory/extractor.py](../karna/memory/extractor.py) — Auto-extraction: regex patterns, dedup, rate-limiting.
- [karna/memory/prompts.py](../karna/memory/prompts.py) — `MEMORY_SYSTEM_PROMPT`: comprehensive instructions for auto-memory behavior.
- [karna/memory/index.py](../karna/memory/index.py) — MEMORY.md index file management.
- [karna/memory/memdir.py](../karna/memory/memdir.py) — Memory directory layout and path utilities.
- [karna/memory/profile.py](../karna/memory/profile.py) — User profile aggregation from memory entries.

## Skills

- [karna/skills/__init__.py](../karna/skills/__init__.py) — Re-exports `Skill`, `SkillManager`, `parse_skill_file`.
- [karna/skills/loader.py](../karna/skills/loader.py) — Skill loader: `.md` parsing with YAML frontmatter, `SkillManager` (load/match/install/create).

## Hooks

- [karna/hooks/__init__.py](../karna/hooks/__init__.py) — Re-exports `HookDispatcher`, `HookType`, `HookResult`.
- [karna/hooks/dispatcher.py](../karna/hooks/dispatcher.py) — `HookDispatcher`: register/dispatch lifecycle hooks, shell-command wrapper, config loading.
- [karna/hooks/builtins.py](../karna/hooks/builtins.py) — Built-in hooks: cost warning, git dirty warning, auto-save memory (stub).

## Permissions

- [karna/permissions/__init__.py](../karna/permissions/__init__.py) — Re-exports `PermissionLevel`, `PermissionManager`, `PermissionRule`, `PROFILES`.
- [karna/permissions/manager.py](../karna/permissions/manager.py) — 3-tier permission system: ALLOW/ASK/DENY per tool, regex patterns, profiles, session grants.

## Compaction

- [karna/compaction/__init__.py](../karna/compaction/__init__.py) — Re-exports `Compactor`.
- [karna/compaction/compactor.py](../karna/compaction/compactor.py) — `Compactor`: auto-compact at 80% of context window, preserve tail, circuit breaker after 3 failures.
- [karna/compaction/prompts.py](../karna/compaction/prompts.py) — Summarization prompt templates for conversation compaction.

## Tokens

- [karna/tokens/__init__.py](../karna/tokens/__init__.py) — Re-exports `TokenCounter`, `count_tokens`.
- [karna/tokens/counter.py](../karna/tokens/counter.py) — Model-aware token counter: tiktoken (optional) with o200k/cl100k selection, `len//4` fallback.

## Security

- [karna/security/__init__.py](../karna/security/__init__.py) — Re-exports all security guards and scrubbers.
- [karna/security/guards.py](../karna/security/guards.py) — `is_safe_path`, `scrub_secrets`, `is_safe_url`, `check_dangerous_command`.
- [karna/security/scrub.py](../karna/security/scrub.py) — Memory-safe scrubbing: secrets + credential paths + base64 blob redaction.

## TUI

- [karna/tui/__init__.py](../karna/tui/__init__.py) — Re-exports `run_repl`.
- [karna/tui/repl.py](../karna/tui/repl.py) — Main REPL: split-pane `prompt_toolkit.Application`, banner, slash dispatch, agent loop, session persistence. Includes `TUIOutputWriter` ring buffer, Esc-to-interrupt, autoscroll lock.
- [karna/tui/output.py](../karna/tui/output.py) — `OutputRenderer`: Rich-based streaming (text/tool calls/results/errors/usage), tool-result panel cap.
- [karna/tui/output_style.py](../karna/tui/output_style.py) — Output styling configuration and theme-aware formatting.
- [karna/tui/input.py](../karna/tui/input.py) — Multiline input: `prompt_toolkit` preferred, plain `input()` fallback, backslash continuation.
- [karna/tui/slash.py](../karna/tui/slash.py) — Slash commands: `/help`, `/model`, `/clear`, `/history`, `/cost`, `/exit`, `/compact`, `/tools`, `/skills`, `/memory`, `/loop`, `/plan`, `/do`, `/system`, `/sessions`, `/resume`, `/paste`, `/copy`, `/tasks`, `/comms`.
- [karna/tui/banner.py](../karna/tui/banner.py) — Startup banner: version, model, tool count in Rich panel with brand colours.
- [karna/tui/themes.py](../karna/tui/themes.py) — Colour theme: Karna brand palette (`#3C73BD` blue, `#87CEEB` sky-blue), Rich `Theme` object.
- [karna/tui/design_tokens.py](../karna/tui/design_tokens.py) — Semantic colour tokens for consistent UI styling.
- [karna/tui/icons.py](../karna/tui/icons.py) — 3-tier icon system (nerd font / emoji / ASCII) with auto-detection.
- [karna/tui/diff.py](../karna/tui/diff.py) — Diff rendering for edit operations.
- [karna/tui/vim.py](../karna/tui/vim.py) — Vim-mode keybinding support.
- [karna/tui/fortunes.py](../karna/tui/fortunes.py) — Random fortune/tip strings for empty-state prompts.
- [karna/tui/completer.py](../karna/tui/completer.py) — Tab completion for slash commands, files, and `/model` provider:model pairs.
- [karna/tui/model_picker.py](../karna/tui/model_picker.py) — Interactive model picker overlay.
- [karna/tui/session_picker.py](../karna/tui/session_picker.py) — Interactive session-resume picker overlay.

## RAG (Retrieval-Augmented Generation)

- [karna/rag/__init__.py](../karna/rag/__init__.py) — Re-exports `KnowledgeStore`.
- [karna/rag/store.py](../karna/rag/store.py) — `KnowledgeStore`: ChromaDB backend when available, JSON+cosine fallback otherwise. Local-only, stored at `~/.karna/knowledge/`.
- [karna/rag/chunker.py](../karna/rag/chunker.py) — Document chunking (`Chunk`, `chunk_file`) for embedding.
- [karna/rag/embedder.py](../karna/rag/embedder.py) — Pluggable embedders: `TFIDFEmbedder` (zero-dep) and `SentenceTransformerEmbedder` (downloads ~384 MB HF model on first use).
- [karna/rag/context.py](../karna/rag/context.py) — RAG context injection for the agent loop.

## Multi-agent Comms

- [karna/comms/__init__.py](../karna/comms/__init__.py) — Re-exports `AgentInbox`, `AgentMessage`.
- [karna/comms/inbox.py](../karna/comms/inbox.py) — `AgentInbox`: file-based inbox at `~/.karna/comms/inbox/{agent}/`, send/check/read/reply.
- [karna/comms/message.py](../karna/comms/message.py) — `AgentMessage`: `.md` files with YAML frontmatter (from/to/timestamp/subject/priority/thread-id).

## Cron Scheduler

- [karna/cron/__init__.py](../karna/cron/__init__.py) — Re-exports `CronJob`, `CronScheduler`, `CronStore`, `YAMLJobStore`, `next_fire_time`, `parse_expression`.
- [karna/cron/scheduler.py](../karna/cron/scheduler.py) — `CronScheduler`: high-level facade used by CLI (`nellie cron …`) and TUI.
- [karna/cron/store.py](../karna/cron/store.py) — `CronJob` model + `CronStore` persistence at `~/.karna/cron/jobs.toml`.
- [karna/cron/expression.py](../karna/cron/expression.py) — Standard 5-field cron expressions + `@daily`/`@hourly` aliases; `parse_expression`, `next_fire_time`, `is_due`.
- [karna/cron/jobs.py](../karna/cron/jobs.py) — `YAMLJobStore`: alternate YAML-backed job store mirror.
- [karna/cron/runner.py](../karna/cron/runner.py) — `JobExecutor`, `run_job`, `scan_and_fire` — executes a job through the agent loop.
- [karna/cron/daemon.py](../karna/cron/daemon.py) — Long-running polling daemon (`nellie cron daemon`).

## Keybindings

- [karna/keybindings/__init__.py](../karna/keybindings/__init__.py) — Keybinding package root.
- [karna/keybindings/defaults.py](../karna/keybindings/defaults.py) — Default keybinding bundle.
- [karna/keybindings/manager.py](../karna/keybindings/manager.py) — Load/merge user keybindings from config.
- [karna/keybindings/apply.py](../karna/keybindings/apply.py) — Apply keybindings to a `prompt_toolkit.KeyBindings` instance.

## Plugin System

- [karna/plugins/__init__.py](../karna/plugins/__init__.py) — Public exports: `KarnaContext`, `Plugin`, `PluginLoader`, `PluginManifestError`.
- [karna/plugins/loader.py](../karna/plugins/loader.py) — Discover `~/.karna/plugins/*`, parse `plugin.toml`, import entry callable, activate with `KarnaContext`.

## Persona Templates

- [templates/KARNA.md](../templates/KARNA.md) — Generic project template.
- [templates/KARNA-engineering.md](../templates/KARNA-engineering.md) — Engineering persona template.
- [templates/KARNA-research.md](../templates/KARNA-research.md) — Research persona template.
- [templates/KARNA-data-science.md](../templates/KARNA-data-science.md) — Data-science persona template.
- [templates/KARNA-bd.md](../templates/KARNA-bd.md) — Business-development persona template.
- [templates/KARNA-health-comms.md](../templates/KARNA-health-comms.md) — Health-comms persona template.

## Tests

Test suite in [tests/](../tests/). Notable files landed on dev:

- [tests/test_browser_tool.py](../tests/test_browser_tool.py) — Browser tool basics.
- [tests/test_comms.py](../tests/test_comms.py) + [tests/test_comms_tool_security.py](../tests/test_comms_tool_security.py) — Inbox + tool security (1 MB cap).
- [tests/test_database_tool.py](../tests/test_database_tool.py) + [tests/test_database_tool_security.py](../tests/test_database_tool_security.py) — DB + SQL-injection / SSRF / cred-scrub guards.
- [tests/test_cron.py](../tests/test_cron.py) — Cron expression parsing + due-time calculations.
- [tests/test_document_tool.py](../tests/test_document_tool.py) — Document reader (docx/xlsx/pdf).
- [tests/test_auto_compact.py](../tests/test_auto_compact.py) — Auto-compaction trigger and circuit breaker.
- [tests/test_background_bash.py](../tests/test_background_bash.py) — Background bash task integration with `TaskRegistry`.
- [tests/test_fork_session.py](../tests/test_fork_session.py) — Session forking.
- [tests/test_moa.py](../tests/test_moa.py) — Multi-model / Mixture-of-Agents tests.
- Full list: see [tests/](../tests/) directory.

## Project Files

- [pyproject.toml](../pyproject.toml) — Build config, dependencies, `nellie` entry point, ruff/pytest settings.
- [install.sh](../install.sh) — Unix installation script.
- [install.ps1](../install.ps1) — Windows PowerShell installation script.
- [Dockerfile](../Dockerfile) — Container build.
- [Makefile](../Makefile) — Convenience targets.
- [LICENSE.md](../LICENSE.md) — MIT license.
- [NOTICES.md](../NOTICES.md) — Attribution notices for ported code (cc-src, hermes-agent).
- [README.md](../README.md) — Project overview.
- [GETTING_STARTED.md](../GETTING_STARTED.md) — First-run guide.
- [CONTRIBUTING.md](../CONTRIBUTING.md) — Contributor guide.
- [SECURITY.md](../SECURITY.md) — Security-reporting policy.
- [CHANGELOG.md](../CHANGELOG.md) — Release changelog.
