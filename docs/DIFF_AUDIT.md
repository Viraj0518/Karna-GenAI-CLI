# Diff Audit: Nellie vs Nellie vs OpenClaw vs Hermes Agent

> Generated 2026-04-17 by gamma from source-level reading of all four codebases.
>
> **Status — 2026-04-20 [alpha]:** portions of this audit are now stale. Several
> items marked P0 / P1 gaps against Nellie have since landed on `dev`. The
> comparison against Nellie / OpenClaw / Hermes was valid at time of
> writing; the "Karna/Nellie" column is the part that has drifted.
>
> **What has landed since this audit** (do not trust the matching rows below):
> - Subagent spawn + isolation → [karna/agents/subagent.py](../karna/agents/subagent.py), [karna/tools/task.py](../karna/tools/task.py)
> - Permission system (ALLOW/ASK/DENY + profiles) → [karna/permissions/manager.py](../karna/permissions/manager.py)
> - Memory system (MEMORY.md + typed memories + auto-extraction) → [karna/memory/](../karna/memory/)
> - Parallel tool execution → [karna/agents/parallel.py](../karna/agents/parallel.py)
> - Hooks system → [karna/hooks/](../karna/hooks/)
> - Skill loader (.md) → [karna/skills/loader.py](../karna/skills/loader.py)
> - Prompt caching (Anthropic) → [karna/providers/caching.py](../karna/providers/caching.py)
> - Background task monitoring → [karna/tools/monitor.py](../karna/tools/monitor.py), [karna/tools/task_registry.py](../karna/tools/task_registry.py)
> - LLM auto-compaction → [karna/compaction/compactor.py](../karna/compaction/compactor.py)
> - Autonomous `/loop` → [karna/agents/autonomous.py](../karna/agents/autonomous.py)
> - Plan mode (`/plan`) → [karna/agents/plan.py](../karna/agents/plan.py)
> - Cron / scheduled tasks → [karna/cron/](../karna/cron/)
> - Notebook (Jupyter) editing → [karna/tools/notebook.py](../karna/tools/notebook.py)
> - Multi-credential pool / failover → [karna/auth/pool.py](../karna/auth/pool.py)
> - Browser (headless Chromium) → [karna/tools/browser.py](../karna/tools/browser.py)
> - Database (SQLite/Postgres/MySQL) → [karna/tools/database.py](../karna/tools/database.py)
> - Document reader (docx/xlsx/pdf/pptx) → [karna/tools/document.py](../karna/tools/document.py)
> - Multi-agent comms → [karna/comms/](../karna/comms/) + [karna/tools/comms.py](../karna/tools/comms.py)
> - RAG knowledge base → [karna/rag/](../karna/rag/)
> - Mixture-of-agents / multi-model verification → see `tests/test_moa.py`
> - Session fork/replay → see `tests/test_fork_session.py`
>
> The single-page inventory in [docs/CODEBASE_MAP.md](CODEBASE_MAP.md) is the
> source of truth for what exists today. A refreshed competitive matrix will
> be produced when the team is ready to pitch externally; the tables below
> remain useful as a historical record of where Nellie stood mid-April 2026.

---

## Section 1: Master Comparison Table

### A. Provider Support

| Feature | Nellie | OpenClaw | Hermes Agent | Karna/Nellie | Gap |
|---|---|---|---|---|---|
| Anthropic (native) | ✅ Primary & only | ❌ Gateway-mediated | ✅ Full adapter + OAuth | ✅ `providers/anthropic.py` | -- |
| OpenAI | ❌ | ❌ | ✅ Direct + compatible | ✅ `providers/openai.py` | -- |
| Azure OpenAI | ❌ | ❌ | ❌ | ✅ `providers/azure.py` | Karna differentiator |
| OpenRouter | ❌ | ❌ | ✅ Primary default | ✅ `providers/openrouter.py` | -- |
| Google / Vertex AI | ❌ | ❌ | ✅ `gemini_cloudcode_adapter.py` | ❌ | P1 gap |
| AWS Bedrock | ❌ | ✅ Extension | ✅ `bedrock_adapter.py` | ❌ | P2 gap |
| Local (Ollama/vLLM/llama.cpp) | ❌ | ❌ | ✅ OpenAI-compat | ✅ `providers/local.py` | -- |
| Nous Portal | ❌ | ❌ | ✅ Native auth | ❌ | Niche |
| Custom OpenAI-compatible | ❌ | ❌ | ✅ `custom_providers` config | ✅ Via local provider | Partial -- needs config UI |
| NVIDIA NIM | ❌ | ❌ | ✅ | ❌ | P2 gap |
| Xiaomi MiMo / Kimi / z.ai | ❌ | ❌ | ✅ Each has adapter | ❌ | Niche |
| HuggingFace Inference | ❌ | ❌ | ✅ | ❌ | P2 gap |
| Multi-credential pool / failover | ❌ | ❌ | ✅ `credential_pool.py` | ❌ | P1 gap |

### B. Tools

| Feature | Nellie | OpenClaw | Hermes Agent | Karna/Nellie | Gap |
|---|---|---|---|---|---|
| Bash/shell execution | ✅ BashTool + sandboxing | ✅ Via gateway | ✅ `terminal_tool.py` | ✅ `tools/bash.py` | -- |
| File read (line numbers) | ✅ FileReadTool | ✅ | ✅ `file_tools.py` | ✅ `tools/read.py` | -- |
| File write | ✅ FileWriteTool | ✅ | ✅ `file_operations.py` | ✅ `tools/write.py` | -- |
| File edit (string replace) | ✅ FileEditTool | ✅ | ✅ `file_operations.py` | ✅ `tools/edit.py` | -- |
| Grep (regex search) | ✅ GrepTool (rg) | ✅ | ✅ `file_tools.py` search | ✅ `tools/grep.py` | -- |
| Glob (file pattern) | ✅ GlobTool | ✅ | ✅ `file_tools.py` list | ✅ `tools/glob.py` | -- |
| Web search | ✅ WebSearchTool | ❌ | ✅ `web_tools.py` | ✅ `tools/web_search.py` | -- |
| Web fetch | ✅ WebFetchTool | ❌ | ✅ `web_tools.py` | ✅ `tools/web_fetch.py` | -- |
| Image/screenshot | ✅ Multimodal vision | ❌ | ✅ Camera handlers | ✅ `tools/image.py` (base64) | Partial -- no screenshot capture |
| Clipboard | ❌ (via MCP) | ❌ | ❌ | ✅ `tools/clipboard.py` | Karna differentiator |
| Notebook (Jupyter) | ✅ NotebookEditTool | ❌ | ❌ | ❌ | P2 gap |
| Task/subagent | ✅ AgentTool + TaskCreate | ❌ | ✅ Subagent spawn | ❌ Stub | P0 gap |
| Monitor (bg process) | ✅ Background tasks | ❌ | ✅ `process_registry.py` | ❌ | P1 gap |
| MCP client | ✅ Full MCP client | ❌ | ✅ MCP + OAuth | ✅ `tools/mcp.py` | -- |
| MCP server (expose tools) | ✅ `entrypoints/mcp.ts` | ❌ | ✅ ACP adapter | ❌ Stub | P1 gap |
| TodoWrite | ✅ TodoWriteTool | ❌ | ✅ `todo_tool.py` | ❌ | P2 gap |
| ToolSearch (deferred) | ✅ ToolSearchTool | ❌ | ❌ | ❌ | P2 gap |
| EnterWorktree | ✅ Git worktree isolation | ❌ | ❌ | ❌ | P2 gap |
| Skill tool | ✅ SkillTool (.md) | ❌ | ✅ `skills_tool.py` | ❌ Stub | P1 gap |
| Browser (headless) | ❌ | ❌ | ✅ `browser_camofox.py` | ❌ | P2 gap |
| Home Assistant | ❌ | ❌ | ✅ `homeassistant_tool.py` | ❌ | Niche |
| TTS/voice | ✅ Voice mode | ✅ Talk mode | ✅ `tts_tool.py` | ❌ | P2 gap |
| Cron/scheduled tasks | ✅ ScheduleCronTool | ❌ | ✅ `cronjob_tools.py` | ❌ | P1 gap |

### C. Agent Loop

| Feature | Nellie | OpenClaw | Hermes Agent | Karna/Nellie | Gap |
|---|---|---|---|---|---|
| Tool-use loop (call->exec->re-prompt) | ✅ Coordinator | ✅ Gateway loop | ✅ `run_agent.py` | ✅ `agents/loop.py` | -- |
| Streaming with tool calls | ✅ Ink SSE | ✅ WebSocket | ✅ SSE | ✅ SSE parsing | -- |
| Parallel tool execution | ✅ | ❌ | ✅ | ❌ Sequential only | P1 gap |
| Max iteration guard | ✅ | ✅ | ✅ | ✅ 25 default | -- |
| Error recovery / retry | ✅ 429/5xx backoff | ✅ | ✅ Jittered backoff | ✅ Exp backoff + jitter | -- |
| Infinite loop detection | ✅ | ❌ | ✅ | ✅ 3 identical calls | -- |
| Context overflow handling | ✅ Auto-compact | ✅ | ✅ LLM summary compress | ✅ FIFO truncation only | Partial -- needs LLM compaction |
| Malformed JSON recovery | ✅ | ❌ | ✅ | ✅ Single-quote fix | -- |
| Empty response retry | ✅ | ❌ | ✅ | ✅ 3 attempts | -- |
| Thinking/extended thinking | ✅ Adaptive effort | ❌ | ✅ Budget per level | ❌ | P1 gap |

### D. UI/UX

| Feature | Nellie | OpenClaw | Hermes Agent | Karna/Nellie | Gap |
|---|---|---|---|---|---|
| Terminal UI framework | ✅ Ink (React-like) | ❌ Gateway+mobile | ✅ Rich + raw ANSI | ✅ Rich | -- |
| Multiline input | ✅ useTextInput | ❌ | ✅ prompt_toolkit | ✅ prompt_toolkit | -- |
| Slash commands | ✅ 50+ commands | ❌ | ✅ 30+ commands | ✅ 14 commands | Partial -- need more |
| History (up-arrow recall) | ✅ useArrowKeyHistory | ❌ | ✅ readline | ✅ prompt_toolkit history | -- |
| Syntax highlighting | ✅ Shiki/Ink | ❌ | ✅ Rich Syntax | ✅ Rich Syntax | -- |
| Spinner/loading | ✅ Animated | ❌ | ✅ Kawaii faces | ✅ Rich Spinner | -- |
| Cost display per turn | ✅ Status bar | ❌ | ✅ Token + cost | ✅ Per-turn dim text | -- |
| Banner/welcome screen | ✅ Product branding | ❌ | ✅ ASCII + tips | ✅ Rich Panel | -- |
| Vim mode | ✅ `/vim` | ❌ | ❌ | ❌ | P2 gap |
| Themes / skins | ✅ `/color` | ❌ | ✅ Skin engine | ✅ `tui/themes.py` basic | Partial |
| Keybinding customization | ✅ keybindings.json | ❌ | ❌ | ❌ | P2 gap |
| Voice input | ✅ `/voice` | ✅ Talk mode | ❌ | ❌ | P2 gap |
| Output style selection | ✅ `/output-style` | ❌ | ✅ Personalities | ❌ | P2 gap |
| Diff display (file edits) | ✅ Side-by-side | ❌ | ✅ Unified diff | ❌ | P1 gap |

### E. Context Management

| Feature | Nellie | OpenClaw | Hermes Agent | Karna/Nellie | Gap |
|---|---|---|---|---|---|
| System prompt builder | ✅ Sectioned + priority | ❌ | ✅ `prompt_builder.py` | ✅ `prompts/system.py` | -- |
| Project context (CLAUDE.md) | ✅ claudemd + hierarchy | ❌ | ✅ AGENTS.md | ✅ KARNA.md + CLAUDE.md | -- |
| Git awareness | ✅ Branch + status + diff | ❌ | ✅ | ✅ `context/git.py` | -- |
| Environment detection | ✅ OS + shell + cwd | ❌ | ✅ | ✅ `context/environment.py` | -- |
| Auto-compaction (LLM summary) | ✅ `/compact` + auto | ❌ | ✅ `context_compressor.py` | ❌ Stub only | P0 gap |
| Context window estimation | ✅ Token counting | ❌ | ✅ Rough estimate | ✅ ~4 chars/token | -- |
| Smart truncation (priority) | ✅ | ❌ | ✅ Head/tail protection | ✅ FIFO from front | Partial |
| Prompt caching | ✅ Anthropic cache | ❌ | ✅ `prompt_caching.py` | ❌ | P1 gap |
| Context references | ✅ | ❌ | ✅ `context_references.py` | ❌ | P2 gap |

### F. Memory + Persistence

| Feature | Nellie | OpenClaw | Hermes Agent | Karna/Nellie | Gap |
|---|---|---|---|---|---|
| Auto-memory (typed) | ✅ memdir with types | ❌ | ✅ Builtin + plugins | ❌ Stub only | P0 gap |
| MEMORY.md index | ✅ `memdir.ts` | ❌ | ✅ Markdown index | ❌ | P0 gap |
| Session persistence (SQLite) | ✅ Session history | ❌ | ✅ FTS5 sessions | ✅ `sessions/db.py` FTS5 | -- |
| Full-text search over sessions | ✅ | ❌ | ✅ | ✅ `sessions/db.py` search | -- |
| Resume previous session | ✅ `/resume` | ❌ | ✅ | ✅ `nellie resume` | -- |
| Cost tracking (per-session) | ✅ Per-model usage | ❌ | ✅ Token + USD | ✅ `sessions/cost.py` | -- |
| Cost tracking (daily/total) | ✅ `/usage` | ❌ | ✅ `/usage` + `/insights` | ✅ `nellie cost` | -- |
| User profile modeling | ❌ | ❌ | ✅ Honcho dialectic | ❌ | P2 gap |

### G. Skills / Plugins

| Feature | Nellie | OpenClaw | Hermes Agent | Karna/Nellie | Gap |
|---|---|---|---|---|---|
| Skill loader (.md files) | ✅ SkillTool | ❌ | ✅ `skills_tool.py` | ❌ Stub | P1 gap |
| Plugin system (Python modules) | ❌ (JS-only) | ✅ Hooks/plugins | ✅ Plugin tools | ❌ Stub | P1 gap |
| Self-improving skills | ❌ | ❌ | ✅ Auto-improve | ❌ | P2 gap |
| agentskills.io compatibility | ❌ | ❌ | ✅ Skills Hub | ❌ | P2 gap |
| Hooks (pre/post tool) | ✅ hooks system | ✅ hooks.ts | ❌ | ❌ Stub | P1 gap |
| Skills Hub (marketplace) | ❌ | ❌ | ✅ GitHub sources | ❌ | P2 gap |

### H. Security

| Feature | Nellie | OpenClaw | Hermes Agent | Karna/Nellie | Gap |
|---|---|---|---|---|---|
| Permission system (ask/allow/deny) | ✅ PermissionContext | ❌ | ✅ `approval.py` | ❌ (safe_mode flag only) | P0 gap |
| Credential management | ✅ OS keychain | ✅ Keychain | ✅ File-based + pool | ✅ `~/.karna/credentials/` | Partial -- no keychain |
| Path traversal guard | ✅ | ❌ | ✅ | ✅ `security/guards.py` | -- |
| SSRF guard | ✅ | ❌ | ✅ | ✅ `security/guards.py` + `web_fetch.py` | -- |
| Dangerous command detection | ✅ | ❌ | ✅ | ✅ `agents/safety.py` | -- |
| Secret scrubbing | ✅ | ✅ Redact module | ✅ | ✅ `security/scrub.py` | -- |
| Zero telemetry | ❌ Analytics present | ❌ | ❌ | ✅ No telemetry | Karna differentiator |
| Container/sandbox execution | ✅ OS-level sandbox | ❌ | ✅ Docker/Singularity/SSH | ❌ | P2 gap |
| Robots.txt respect | ❌ | ❌ | ❌ | ✅ `web_fetch.py` | Karna differentiator |

### I. Advanced

| Feature | Nellie | OpenClaw | Hermes Agent | Karna/Nellie | Gap |
|---|---|---|---|---|---|
| Subagent spawn + isolation | ✅ AgentTool | ❌ | ✅ Spawn subagents | ❌ | P0 gap |
| Git worktree isolation | ✅ EnterWorktreeTool | ❌ | ❌ | ❌ | P2 gap |
| Autonomous loop (/loop) | ❌ | ❌ | ✅ Batch mode | ❌ | P1 gap |
| Multi-model verification | ❌ | ❌ | ✅ `mixture_of_agents_tool.py` | ❌ | P2 gap |
| Cost-aware routing | ❌ | ❌ | ❌ | ❌ Planned | P1 gap |
| Fork/replay conversations | ✅ | ❌ | ❌ | ❌ | P2 gap |
| Cron/scheduled tasks | ✅ ScheduleCronTool | ❌ | ✅ `cron/` module | ❌ | P1 gap |
| Plan mode (thinking first) | ✅ EnterPlanModeTool | ❌ | ❌ | ❌ | P2 gap |
| Reasoning effort control | ✅ `/effort` | ❌ | ✅ `/effort` | ❌ | P1 gap |

### J. Deployment

| Feature | Nellie | OpenClaw | Hermes Agent | Karna/Nellie | Gap |
|---|---|---|---|---|---|
| pip installable | ❌ | ❌ | ✅ `pip install` | ✅ `pyproject.toml` | -- |
| npm installable | ✅ `npm i -g @anthropic-ai/claude-code` | ✅ npm | ❌ | ❌ N/A | -- |
| Docker image | ❌ | ✅ Dockerfile | ✅ Docker env | ❌ | P2 gap |
| Homebrew | ❌ | ❌ | ❌ | ❌ | -- |
| IDE extensions (VS Code) | ✅ Official extension | ❌ | ❌ | ❌ | P2 gap |
| IDE extensions (JetBrains) | ✅ Official plugin | ❌ | ❌ | ❌ | P2 gap |
| Web app | ❌ | ✅ iOS/Android apps | ❌ | ❌ | P2 gap |
| Mobile gateway (Telegram) | ❌ | ✅ iOS/Android native | ✅ Telegram/Discord/Slack/WhatsApp/Signal | ❌ Stub | P1 gap |
| Install script (curl) | ✅ | ❌ | ✅ | ❌ | P2 gap |
| Android app | ❌ | ✅ Native Kotlin | ❌ | ❌ | Out of scope |

### K. Ecosystem

| Feature | Nellie | OpenClaw | Hermes Agent | Karna/Nellie | Gap |
|---|---|---|---|---|---|
| MCP server (expose tools) | ✅ `entrypoints/mcp.ts` | ❌ | ✅ ACP adapter | ❌ Stub | P1 gap |
| MCP client (consume servers) | ✅ | ❌ | ✅ | ✅ `tools/mcp.py` | -- |
| UNBLOCK integration | ❌ | ❌ | ❌ | ❌ Planned | P2 gap |
| GitHub integration | ✅ GitHub App, PR review | ❌ | ✅ Skills Hub GitHub | ❌ | P2 gap |
| Slack/Discord bot | ✅ Slack App install | ❌ | ✅ Full gateway | ❌ | P1 gap |
| ACP (Agent Client Protocol) | ❌ | ✅ Full ACP bridge | ✅ ACP adapter | ❌ | P2 gap |

---

## Section 2: What Nellie Has That We Don't (Yet)

Ranked by impact (user value x frequency of need):

### P0 -- Must Have

| # | Feature | How upstream Implements It | Effort (hrs) | Priority |
|---|---|---|---|---|
| 1 | **Auto-compaction (LLM summarization)** | `commands/compact/compact.ts` sends middle turns to the model with a structured summary template, protects head (system) and tail (recent) messages. Triggers automatically when context exceeds 80% of window. | 12 | P0 |
| 2 | **Subagent spawn + isolation** | `AgentTool` creates a child agent with its own conversation context, tool subset (no nesting beyond depth), and isolated working directory. Results are returned to the parent as tool output. | 16 | P0 |
| 3 | **Permission system (ask/allow/deny)** | `hooks/toolPermission/` -- every tool call runs through a PermissionContext that checks against user-configured allow/deny rules. Interactive mode prompts the user; headless mode uses config. Three-tier: global settings, project settings, session overrides. | 12 | P0 |
| 4 | **Memory system (MEMORY.md + typed memories)** | `memdir/memdir.ts` manages `~/.claude/memory/` with MEMORY.md index, typed categories (user/feedback/project/reference), frontmatter-based metadata, relevance scoring via grep, and automatic pruning. | 16 | P0 |

### P1 -- Important

| # | Feature | How upstream Implements It | Effort (hrs) | Priority |
|---|---|---|---|---|
| 5 | **Parallel tool execution** | Agent loop collects all tool_calls from a single assistant turn and executes them concurrently via `Promise.all`, then sends all results in a single tool message. | 4 | P1 |
| 6 | **Hooks system (pre/post tool)** | `hooks/` directory with `settings.json` configuration. Hooks run shell commands before/after tool execution, on errors, and at session start/end. | 8 | P1 |
| 7 | **Diff display for file edits** | Captures before/after file content around edit operations and renders side-by-side or unified diffs in the terminal using Ink components. | 6 | P1 |
| 8 | **Prompt caching (Anthropic)** | Marks system prompt and early conversation turns with `cache_control: {"type": "ephemeral"}` breakpoints. Tracks cache hit rate via `cache_read_input_tokens` / `cache_creation_input_tokens`. | 4 | P1 |
| 9 | **Skill loader (.md instructions)** | `SkillTool` loads `.md` files from `~/.claude/skills/` and project `.claude/skills/`. Each skill file contains instructions the model follows when invoked via `/skill-name`. | 8 | P1 |
| 10 | **Background task monitoring** | Tracks background processes, allows streaming stdout, and notifies when tasks complete. Used for long-running builds and tests. | 8 | P1 |
| 11 | **Reasoning effort control** | `/effort` command sets `budget_tokens` for extended thinking. Adaptive effort levels map to different thinking budgets. | 4 | P1 |
| 12 | **Cron/scheduled tasks** | `ScheduleCronTool` with create/list/delete operations. Jobs persist across sessions and execute on schedule. | 10 | P1 |
| 13 | **MCP server mode** | `entrypoints/mcp.ts` exposes upstream's tools as an MCP server so other agents can call them. | 8 | P1 |

### P2 -- Nice to Have

| # | Feature | How upstream Implements It | Effort (hrs) | Priority |
|---|---|---|---|---|
| 14 | **Notebook (Jupyter) editing** | `NotebookEditTool` parses `.ipynb` JSON, modifies cells, and writes back. | 8 | P2 |
| 15 | **Git worktree isolation** | `EnterWorktreeTool` creates a git worktree for isolated changes, `ExitWorktreeTool` merges back. | 10 | P2 |
| 16 | **Plan mode** | `EnterPlanModeTool` switches the agent into a read-only thinking mode where it plans before acting. | 6 | P2 |
| 17 | **Vim mode** | `/vim` command enables vim-style keybindings in the input area. | 4 | P2 |
| 18 | **Voice input/output** | `/voice` command enables voice interaction via microphone input and TTS output. | 16 | P2 |
| 19 | **IDE integration (VS Code)** | Full VS Code extension with Nellie panel, inline suggestions, and terminal integration. | 40+ | P2 |
| 20 | **Keybinding customization** | `~/.claude/keybindings.json` for user-defined key mappings. | 4 | P2 |
| 21 | **TodoWrite tool** | Structured todo/task tracking within the agent context. | 4 | P2 |
| 22 | **ToolSearch (deferred loading)** | Loads tool schemas on demand to reduce initial context size. | 6 | P2 |

---

## Section 3: What Nellie Has That Nellie Doesn't

These are our **differentiators** -- features where Nellie is ahead or serves a different niche:

### 1. Model-Agnostic Architecture
upstream is locked to Anthropic's API. Nellie supports **6 providers** out of the box (Anthropic, OpenAI, Azure, OpenRouter, Local, and any OpenAI-compatible endpoint). Users can switch models mid-conversation with `/model`. This is the single biggest differentiator.

### 2. Azure OpenAI First-Class Support
upstream has zero Azure support. Nellie has a full `AzureOpenAIProvider` with deployment-based routing, `api-key` header auth, and Azure-specific endpoint construction. Critical for enterprise users.

### 3. Web Search Built-In (No MCP Required)
upstream requires an MCP server for web search. Nellie has `WebSearchTool` with a cascading backend (DuckDuckGo -> Brave -> SearXNG) that works out of the box with zero configuration and zero API keys.

### 4. Web Fetch with robots.txt Respect
Nellie's `WebFetchTool` checks `robots.txt` before fetching, which upstream does not. This is both more ethical and reduces the chance of IP blocks.

### 5. Self-Hosted / Privacy-First
- **Zero telemetry**: Nellie sends nothing to any developer service. upstream has analytics (`logEvent` calls throughout).
- **Local credential storage**: All credentials in `~/.karna/credentials/` with 0600 permissions. upstream uses Anthropic's auth flow.
- **No account required**: Bring your own API key. upstream requires an Anthropic account.

### 6. Proprietary Internal Tool
upstream is proprietary (Anthropic copyright, source-available but not OSS). Nellie is an internal Karna tool.

### 7. Clipboard Tool Built-In
upstream has no native clipboard support (requires MCP). Nellie has a cross-platform `ClipboardTool` (macOS pbcopy/pbpaste, Linux xclip/wl-copy, WSL) with `/paste` and `/copy` slash commands.

### 8. Cost-Aware Routing (Planned)
upstream has no concept of budget limits or cost-aware model selection. Nellie's architecture (multi-provider + pricing table in `sessions/cost.py`) enables routing queries to cheaper models when budget is tight -- a planned feature with the infrastructure already in place.

### 9. Python-Native
Written in Python, installable via `pip`, extensible via Python modules. upstream is TypeScript/Node.js. For the Python ecosystem (data science, ML, DevOps), a Python agent is a natural fit.

### 10. Per-Model Prompt Adaptation
`prompts/system.py` automatically adapts the system prompt based on the model (Claude vs GPT vs weak local models), adding model-specific instructions. upstream has a single Anthropic-optimized prompt.

---

## Section 4: What Hermes Has That We Should Steal

### 1. LLM Context Compaction (P0 -- 12 hrs)
`context_compressor.py` is the gold standard. Key innovations:
- **Structured summary template** with Resolved/Pending/Active Task sections
- **"Different assistant" handoff framing** to prevent the model from re-executing summarized tasks
- **Tool output pruning pre-pass** (cheap pre-compression) before LLM summarization
- **Scaled summary budget** proportional to compressed content length
- **Iterative summary updates** that preserve information across multiple compactions
- **Head/tail token-budget protection** instead of fixed message counts

### 2. Credential Pool with Failover (P1 -- 10 hrs)
`credential_pool.py` supports multiple API keys per provider with:
- **Fill-first, round-robin, random, and least-used** selection strategies
- **Automatic cooldown** after 429 or 402 errors (1-hour default)
- **Retry-After header parsing** for provider-suggested delays
- **Status tracking** per credential (ok, exhausted, with reset timestamps)

### 3. Self-Improving Skills (P2 -- 16 hrs)
`skills_tool.py` + `skills_hub.py`:
- Skills auto-improve after complex tasks
- Skills Hub with GitHub-based source adapters
- Content hashing + quarantine for security
- Audit logging for skill installations

### 4. Messaging Gateway (P1 -- 20 hrs for Telegram)
`hermes_cli/gateway.py` + platform adapters:
- Single gateway process serves Telegram, Discord, Slack, WhatsApp, Signal
- Voice memo transcription
- Cross-platform conversation continuity
- DM pairing for security

### 5. Cron Scheduler (P1 -- 10 hrs)
`cron/jobs.py` + `tools/cronjob_tools.py`:
- Natural language schedule specification
- Prompt injection scanning for cron job content
- Delivery to any connected platform
- Pause/resume/trigger operations

### 6. Terminal Environment Backends (P2 -- 16 hrs)
`tools/environments/`:
- **6 backends**: Local, Docker, SSH, Daytona, Singularity, Modal
- Serverless persistence (Daytona/Modal -- hibernate when idle)
- File sync between host and remote environments
- GPU cluster support via Modal

### 7. Display Improvements
- **Kawaii faces** for spinner states (fun but effective UX)
- **Diff display** with skin-aware ANSI coloring for file edits
- **Tool preview formatting** with 1-line summaries before execution

### 8. Rate Limit Tracker (P1 -- 4 hrs)
`agent/rate_limit_tracker.py` + `agent/nous_rate_guard.py`:
- Per-provider rate limit tracking
- Automatic backoff with Retry-After parsing
- Nous Portal subscription-aware rate limiting

---

## Section 5: What OpenClaw Has That We Should Steal

OpenClaw is architecturally very different from Nellie -- it's a gateway-based system with native iOS/Android apps, not a CLI agent. Relevant patterns:

### 1. ACP (Agent Client Protocol) Bridge (P2 -- 20 hrs)
`docs.acp.md` + `src/` ACP implementation:
- Standardized protocol for IDE integration
- Session mapping across reconnects
- Slash command advertisement to clients
- Would enable Karna to be driven by any ACP-compatible editor

### 2. Hook System Architecture (P1 -- 8 hrs)
`src/hooks/`:
- **Bundled hooks** that ship with the product
- **Plugin hooks** loaded from user directories
- **Policy engine** for hook execution rules
- **Fire-and-forget** hooks for non-critical operations
- Well-structured hook lifecycle (install, update, workspace)

### 3. Mobile-First Chat UI Patterns (Reference only)
`apps/shared/OpenClawKit/Sources/OpenClawChatUI/`:
- `ChatViewModel.swift` -- clean MVVM for streaming chat
- `ChatTransport.swift` -- WebSocket transport abstraction
- `ChatSessions.swift` -- session management patterns
- Not directly portable (Swift), but the architecture patterns are informative for a future Karna web/mobile UI

### 4. Talk/Voice Mode Architecture (P2)
`apps/shared/OpenClawKit/Sources/OpenClawKit/TalkCommands.swift`:
- Directive-based voice control
- ElevenLabs TTS integration
- Wake word detection
- Voice prompt builder with history timestamps

### 5. Canvas (Agent-to-UI Actions) (Reference only)
`CanvasA2UIAction.swift` + `CanvasA2UICommands.swift`:
- Agent can programmatically create HTML/JS canvases
- Actions bridge for agent-to-UI communication
- Snapshot capability for visual content

### 6. Logging / Redaction (P2 -- 4 hrs)
`src/logging/redact.ts`:
- Bounded identifier redaction
- Structured log parsing and filtering
- Diagnostic session state capture
- Could improve Karna's logging story

---

## Section 6: Top 10 Features to Build Next

Ranked by (user value x feasibility / effort):

| Rank | Feature | Source | Value | Effort | Score | Notes |
|---|---|---|---|---|---|---|
| 1 | **LLM auto-compaction** | upstream + Hermes | 10 | 12 hrs | 10 | Blocking for long sessions. Port Hermes's structured summary approach. |
| 2 | **Permission system** | upstream | 9 | 12 hrs | 9 | Security requirement. Three-tier: global/project/session allow/deny rules. |
| 3 | **Memory system (MEMORY.md)** | upstream | 9 | 16 hrs | 8.5 | Typed memories with index file. Critical for multi-session continuity. |
| 4 | **Parallel tool execution** | upstream | 8 | 4 hrs | 8 | Low effort, high impact. `asyncio.gather` the tool calls. |
| 5 | **Subagent spawn** | upstream | 8 | 16 hrs | 7.5 | Enables complex multi-step tasks. Child agent with isolated context. |
| 6 | **Diff display for file edits** | upstream + Hermes | 7 | 6 hrs | 7 | UX improvement. Show before/after when edit tool runs. |
| 7 | **Hooks system** | upstream + OpenClaw | 7 | 8 hrs | 7 | Extensibility. Pre/post tool hooks configured in settings. |
| 8 | **Prompt caching (Anthropic)** | upstream | 7 | 4 hrs | 7 | Cost reduction. Mark system prompt + early turns with cache_control. |
| 9 | **Skill loader (.md files)** | upstream + Hermes | 6 | 8 hrs | 6 | Reusable task-specific instructions loaded on demand. |
| 10 | **Google/Vertex AI provider** | Hermes | 6 | 8 hrs | 6 | Gemini 2.5 Pro is competitive. OpenAI-compat adapter via AI Studio API. |
