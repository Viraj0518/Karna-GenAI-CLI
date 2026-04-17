# Nellie Developer Guide -- Karna Engineering

> Nellie (CLI binary: `nellie`) is Karna's internal AI agent harness.
> It sends zero telemetry. The only network traffic is provider API calls,
> web search/fetch (user-initiated), and MCP connections (user-configured).

---

## Architecture Overview

```
                         User
                          |
                     [TUI / REPL]
                    karna/tui/repl.py
                          |
         +----------------+----------------+
         |                |                |
    Slash Commands   Multiline Input   Output Renderer
    tui/slash.py     tui/input.py      tui/output.py
                          |
                   [Agent Loop]
                 agents/loop.py
                   |         |
          +--------+    +----+----+
          |              |         |
     [Provider]     [Tool Dispatch]  [Safety]
     providers/     tools/           agents/safety.py
     base.py        base.py
          |              |
     API Call       Tool Execution
     (streaming)    (parallel/sequential)
          |              |
     [StreamEvent]  [ToolResult]
          |              |
          +------+-------+
                 |
          [Conversation]
           models.py
                 |
     +-----------+-----------+
     |           |           |
  [Sessions]  [Memory]   [Context]
  sessions/   memory/    context/
  db.py       manager.py manager.py
```

### Request Flow

1. **User types a prompt** in the TUI REPL (`tui/repl.py`).
2. The REPL checks for slash commands (`/help`, `/model`, etc.) and dispatches them locally.
3. For regular messages, the REPL appends a `Message(role="user")` to the `Conversation`.
4. The **system prompt** is built by `prompts/system.py`, incorporating:
   - Base template (per-provider: `default.txt`, `anthropic.txt`, `weak_model.txt`)
   - Auto-generated tool documentation from the tool registry
   - Project context (KARNA.md, CLAUDE.md, .cursorrules detection)
   - Git context (branch, status, recent commits)
   - Environment context (platform, shell, cwd, date)
   - Memory context (MEMORY.md index + recent memory entries)
   - Model-specific adaptations (Claude XML hints, GPT function-call hints, weak-model reminders)
5. The **agent loop** (`agents/loop.py`) streams the conversation to the provider.
6. If the model returns **tool calls**, the loop:
   - Resolves each call to a `BaseTool` instance
   - Runs the 3-tier **permission check** (ALLOW/ASK/DENY)
   - Runs the **pre-tool safety check** (dangerous commands, sensitive paths, SSRF)
   - Executes tools -- parallel for read-only tools, sequential for mutating tools
   - Appends tool results to the conversation
   - Loops back to step 5
7. If the model returns **text only** (no tool calls), the response is complete.
8. The TUI's `OutputRenderer` renders text deltas, tool call panels, and cost info via Rich.
9. The message is persisted to the **session database** (SQLite FTS5).
10. **Hooks** fire at lifecycle points (cost warning, git dirty, auto-memory).

### Key Design Decisions

- **No telemetry, ever.** All data stays on-disk under `~/.karna/`.
- **Provider-agnostic.** The `Provider` protocol is structural (duck-typed). Any backend that implements `complete()`, `stream()`, and `list_models()` works.
- **Tool isolation.** Tools with `sequential=True` (bash, write, edit) never run concurrently. Read-only tools (read, grep, glob) run in parallel via `asyncio.gather`.
- **Graceful degradation.** tiktoken, trafilatura, nbformat, prompt-toolkit are all optional. The system works without them using fallbacks.
- **Security by default.** HTTPS enforced for non-local providers. Credentials stored mode 0600. Secrets scrubbed from output. SSRF blocked. Dangerous commands detected.

---

## Module Reference

### karna/__init__.py

Package root. Defines `__version__` and the privacy notice. No telemetry, no analytics.

### karna/cli.py

Typer CLI entry point exposed as the `nellie` console script (see `pyproject.toml` `[project.scripts]`).

**Commands:**
| Command | Description |
|---|---|
| `nellie` (no args) | Launch interactive REPL |
| `nellie auth login <provider>` | Authenticate with a provider (stub) |
| `nellie model` | Show active model |
| `nellie model set <provider:model>` | Set active model |
| `nellie config show` | Dump configuration table |
| `nellie history` | List recent sessions |
| `nellie history search <query>` | FTS5 search across all sessions |
| `nellie history show <id>` | Replay a session |
| `nellie history delete <id>` | Delete a session |
| `nellie resume [id]` | Resume a previous session |
| `nellie cost` | Show cost summary (today/week/all-time/by-model) |
| `nellie mcp add/list/remove/test` | Manage MCP servers |
| `nellie init` | Initialize KARNA.md for current project |

**How to add a new command:**
1. Define a handler function with `@app.command()` or `@subgroup.command()`.
2. Use `typer.Argument` / `typer.Option` for parameters.
3. Import lazily to keep startup fast.

### karna/agents/loop.py

The heart of Nellie. Implements the iterative tool-call cycle.

**Key functions:**
- `agent_loop()` -- async generator yielding `StreamEvent` objects. Streaming variant.
- `agent_loop_sync()` -- returns a single `Message`. Non-streaming variant.
- `_execute_tool_calls()` -- dispatches a batch of tool calls, parallel for read-only, sequential for mutating.
- `_call_provider_with_retry()` -- wraps provider streaming with exponential backoff on 429/5xx.

**Error recovery (all built-in):**
- Granular tool errors (timeout, permission, file-not-found) surfaced to model
- Provider retry with jittered backoff (429, 5xx, connection errors)
- Malformed tool-call JSON repair (single quotes, trailing commas)
- Infinite tool-call loop detection (3 identical consecutive calls)
- Empty/null response retry with nudge message
- Context overflow auto-truncation

**Constants:**
- `_LOOP_DETECTION_THRESHOLD = 3` -- identical calls before breaking
- `_DEFAULT_TOOL_TIMEOUT = 120` -- seconds per tool execution

### karna/agents/subagent.py

Independent agents with their own conversation context, tools, and optional git worktree isolation.

**Classes:**
- `SubAgent` -- a single background agent. Supports `run()` (blocking) and `run_in_background()` (returns `asyncio.Task`). Can create/cleanup git worktrees for filesystem isolation.
- `SubAgentManager` -- registry of spawned subagents. Provides `spawn()`, `get()`, `list_active()`, `list_all()`.

### karna/agents/safety.py

Pre-execution safety checks run before every tool call.

**Checks:**
- **Bash:** Dangerous command detection (rm -rf /, dd of=/dev, fork bombs, curl|sh, force-push to main)
- **Read/Edit/Write:** Sensitive path blocking (/etc/shadow, ~/.ssh/, credentials files, .pem/.key files)
- **Web_fetch:** SSRF guard (blocks localhost, RFC-1918, link-local, cloud metadata endpoints)

**Entry point:** `pre_tool_check(tool, args) -> (proceed: bool, warning: str | None)`

### karna/providers/

Provider system. Each provider implements `BaseProvider` (abstract class) with `complete()`, `stream()`, and `list_models()`.

#### karna/providers/__init__.py

Registry mapping provider names to classes via lazy imports. Key exports:
- `get_provider(name)` -- instantiate a provider by name
- `get_provider_class(name)` -- get the class without instantiating
- `resolve_model("provider:model")` -- parse model strings, default to openrouter

Registered providers: `openrouter`, `openai`, `azure`, `anthropic`, `local`.

#### karna/providers/base.py

Abstract base class. Features:
- Credential loading from `~/.karna/credentials/<provider>.token.json`
- Retry with jittered exponential backoff (ported from hermes-agent, MIT)
- Rate-limit handling with Retry-After header parsing
- Per-call cost tracking via `_track_usage()` / `_make_usage()`
- HTTPS enforcement for non-local URLs
- TLS verification always enabled
- Request/response bodies NEVER logged (security invariant)

#### karna/providers/anthropic.py

Anthropic Messages API provider (`https://api.anthropic.com/v1/messages`).
- Native Anthropic message format (not OpenAI-compatible)
- System prompt as top-level parameter
- SSE streaming (message_start, content_block_delta, message_delta)
- Prompt caching via `cache_control` markers on system prompt and tools
- Cache-adjusted cost calculation (reads at 10%, writes at 125%)
- Per-model output token limits (claude-opus-4: 128K, claude-sonnet-4: 64K)

#### karna/providers/openrouter.py

Primary backend. OpenAI-compatible chat completions via `https://openrouter.ai/api/v1`.
- Model aliases (e.g., `gpt-4o` -> `openai/gpt-4o`, `claude-sonnet-4` -> `anthropic/claude-sonnet-4-20250514`)
- SSE streaming with tool call accumulation
- Cost from OpenRouter's `total_cost` field when available

#### karna/providers/openai.py

Standard OpenAI chat completions (`https://api.openai.com/v1`).
- Function calling format
- `stream_options: {include_usage: true}` for streaming usage

#### karna/providers/azure.py

Azure OpenAI (`{endpoint}/openai/deployments/{deployment}/chat/completions`).
- `api-key` header auth (not Bearer)
- Deployment-based routing
- OpenAI pricing for cost estimates

#### karna/providers/local.py

OpenAI-compatible local servers (llama.cpp, vLLM, Ollama, LM Studio).
- Default `http://localhost:8080/v1`
- No auth required (optional key)
- 300s timeout (local models can be slow)
- No cost tracking (local = free)

#### karna/providers/caching.py

`PromptCache` helper for provider-level prompt caching.
- Anthropic: adds `cache_control: {type: "ephemeral"}` to system prompt and last tool definition
- OpenAI/generic: sorts tools by name for prefix stability (auto-cached by backend)
- Tracks cache hit/miss statistics

**How to add a new provider:**
1. Create `karna/providers/myprovider.py` with a class inheriting `BaseProvider`.
2. Implement `complete()`, `stream()`, `list_models()`.
3. Register in `karna/providers/__init__.py` `_PROVIDER_PATHS` dict.
4. Add credential resolution in `__init__` (env var + token file).

### karna/tools/

Tool system. Every tool inherits `BaseTool` and implements `async execute(**kwargs) -> str`.

#### karna/tools/__init__.py

Registry mapping tool names to `(module, class)` tuples. Key exports:
- `get_tool(name)` -- instantiate a single tool
- `get_all_tools()` -- instantiate all registered tools
- `TOOLS` dict for introspection

#### karna/tools/base.py

Abstract base class. Fields: `name`, `description`, `parameters` (JSON Schema), `sequential` (bool).
- `sequential=True` means the tool must never run concurrently (bash, write, edit).
- `to_openai_tool()` / `to_anthropic_tool()` -- format converters for API payloads.

#### Tool Inventory

| Tool | File | Sequential | Description |
|---|---|---|---|
| `bash` | `bash.py` | Yes | Shell command execution with cwd tracking, output truncation, dangerous-command detection |
| `read` | `read.py` | No | File reading with line numbers, offset+limit, binary/image detection, path safety |
| `write` | `write.py` | Yes | File creation/overwriting with auto-mkdir, path safety |
| `edit` | `edit.py` | Yes | Exact string replacement with uniqueness check, replace_all support |
| `grep` | `grep.py` | No | Regex content search via ripgrep (fallback: grep -rn), glob filters, context lines |
| `glob` | `glob.py` | No | File pattern matching via pathlib, git-aware (.gitignore), sorted by mtime |
| `web_search` | `web_search.py` | No | Privacy-first search: DuckDuckGo (default), Brave Search, SearXNG cascade |
| `web_fetch` | `web_fetch.py` | No | URL fetching with SSRF guard, robots.txt respect, trafilatura extraction |
| `clipboard` | `clipboard.py` | No | Cross-platform clipboard read/write (macOS/X11/Wayland/WSL) |
| `image` | `image.py` | No | Image inclusion for vision models -- base64 encoding with marker protocol |
| `git` | `git_ops.py` | Yes | Structured git operations with safety guards (blocks force-push, reset --hard) |
| `monitor` | `monitor.py` | No | Background process streaming -- each stdout line becomes a notification event |
| `notebook` | `notebook.py` | Yes | Jupyter .ipynb read/edit/add/execute/create (nbformat optional) |
| `task` | `task.py` | No | Spawn background subagents with optional git worktree isolation |
| `mcp` | `mcp.py` | No | MCP server connection, tool discovery, and proxying via JSON-RPC over stdio |

**How to add a new tool:**
1. Create `karna/tools/mytool.py` with a class inheriting `BaseTool`.
2. Set `name`, `description`, `parameters` (JSON Schema), optionally `sequential=True`.
3. Implement `async execute(**kwargs) -> str`.
4. Register in `karna/tools/__init__.py` `_TOOL_PATHS` dict.
5. Optionally add guidance in `karna/prompts/tool_descriptions.py` `_TOOL_GUIDANCE`.

### karna/auth/

Credential management. All credentials stored under `~/.karna/credentials/` with mode 0600.

#### karna/auth/credentials.py

CRUD for provider API keys stored as JSON files (`<provider>.token.json`).
- `save_credential()` -- writes with umask 0077, chmod 0600
- `load_credential()` -- reads and returns dict (empty if missing)
- `load_credential_pool()` -- wraps in a `CredentialPool`
- `check_credential_permissions()` -- audits file modes, returns warnings
- Credential values are NEVER logged in full (first 8 chars only)

#### karna/auth/pool.py

Multi-key credential pool with automatic failover (ported from hermes-agent, MIT).
- Supports single-key (`{"api_key": "..."}`) and multi-key (`{"keys": [...], "strategy": "..."}`) formats
- Strategies: `failover` (default), `round-robin`, `least-used`
- Rate-limited keys enter timed cooldown; auth-failed keys permanently removed
- `AllKeysExhaustedError` raised when no keys available

### karna/config.py

Configuration management via `~/.karna/config.toml`.

**`KarnaConfig` model (Pydantic):**
| Field | Default | Description |
|---|---|---|
| `active_model` | `openrouter/auto` | Currently active model |
| `active_provider` | `openrouter` | Provider name |
| `system_prompt` | `You are Nellie...` | Default system prompt |
| `max_tokens` | 4096 | Max tokens for completion |
| `temperature` | 0.7 | Sampling temperature |
| `safe_mode` | false | Block (vs warn) dangerous commands |

`load_config()` loads from disk (creates defaults if missing), runs permission checks.
`save_config()` persists to disk with mode 0644.

### karna/models.py

Core data models shared across the entire codebase.

**Classes:**
- `ToolCall` -- a tool invocation (id, name, arguments)
- `ToolResult` -- result from tool execution (tool_call_id, content, is_error)
- `Message` -- a single conversation turn (role, content, tool_calls, tool_results)
- `Conversation` -- ordered message list with model/provider metadata
- `StreamEvent` -- streaming event (text, tool_call_start/delta/end, done, error)
- `Usage` -- token counts and cost for a single API call
- `ModelInfo` -- metadata for a provider's model
- `Provider` -- structural protocol (duck-typed interface) every provider must satisfy

**`PRICING_TABLE`:** Per-million-token pricing for cost estimation. Used by `estimate_cost()` with longest-prefix matching.

### karna/context/

Context assembly for every provider call.

#### karna/context/manager.py

`ContextManager` -- builds the full message list within the context window budget.
- Injects project context, git context, environment context
- Truncates oldest messages (FIFO) to fit token budget, always preserving the last user message
- Called by the REPL before every agent loop turn

#### karna/context/project.py

`ProjectContext` -- walks up from cwd looking for instruction files.
- Priority order: KARNA.md > CLAUDE.md > .karna/project.toml > .cursorrules > .github/copilot-instructions.md
- All matching files are loaded and concatenated (closest shadows farthest)
- Supports both markdown and TOML formats

#### karna/context/git.py

`GitContext` -- injects repo state (branch, status summary, recent commits, diff stat).
- Runs 5 git commands in parallel via `asyncio.gather`
- Truncates large status output to 2000 chars
- Summarizes status into one-line counts (e.g., "3 modified, 1 untracked")

#### karna/context/environment.py

`EnvironmentContext` -- platform, shell, Python version, cwd, date.
- Uses `distro` for Linux distribution name (optional)
- Detects shell from `$SHELL` env var

### karna/prompts/

System prompt assembly.

#### karna/prompts/system.py

The most critical module. Assembles the system prompt from:
1. Template selection (per-provider: `default.txt`, `anthropic.txt`, `weak_model.txt`)
2. Auto-generated tool documentation from the registry
3. Context sections injected in priority order
4. Token budget trimming (lowest-priority sections dropped first)
5. Model-specific adaptations

**Priority order for trimming:**
1. Identity + tools (always kept)
2. Environment (always kept)
3. Custom instructions
4. Project context
5. Git context
6. Memory context (trimmed first)

**Model adaptations:**
- Claude: XML tag hints, native tool_use reminder
- GPT/O3: function_call format, JSON structured output hints
- Weak models (phi, qwen2, gemma, small llama): explicit tool-use reminders

#### karna/prompts/tool_descriptions.py

Auto-generates tool documentation sections from the tool registry.
- Per-tool guidance tables (when to use, when NOT to use)
- Parameter summaries from JSON Schema
- Generic fallback for tools without custom guidance

### karna/sessions/

Session persistence and cost tracking. All data in `~/.karna/sessions/sessions.db`.

#### karna/sessions/db.py

`SessionDB` -- SQLite database with WAL mode and FTS5 full-text search.
- **Tables:** `sessions` (id, timestamps, model, cost totals), `messages` (role, content, tool_calls/results as JSON)
- **FTS5:** `messages_fts` virtual table with auto-sync triggers on insert/update/delete
- `search(query)` -- full-text search across all historical messages
- `resume_session(id)` -- reconstruct a `Conversation` from stored messages

#### karna/sessions/cost.py

`CostTracker` -- accumulates token usage and cost per session.
- `compute_cost()` -- cascading cost lookup: provider SDK > local PRICING table > `models.estimate_cost` > conservative default
- Persists each update to the session database (crash-safe)
- Aggregate queries: today, weekly, all-time, by-model

### karna/memory/

Persistent file-based memory system with 4 typed entries.

#### karna/memory/types.py

**Memory types:**
- `user` -- information about the user (role, goals, preferences)
- `feedback` -- corrections and validated approaches
- `project` -- ongoing work context not derivable from code/git
- `reference` -- pointers to external systems (Linear, Slack, dashboards)

`MemoryEntry` -- Pydantic model with name, description, type, content, timestamps, file_path.

#### karna/memory/manager.py

`MemoryManager` -- CRUD + search + context injection for memory files.
- Layout: `~/.karna/memory/MEMORY.md` (index) + individual `.md` files with YAML frontmatter
- `load_all()` -- parse all memory files, sorted newest-first
- `save_memory()` -- write file with frontmatter, update MEMORY.md index
- `search(query)` -- keyword search ranked by hit count
- `get_context_for_prompt()` -- build memory section for system prompt within token budget
- `check_staleness()` -- warn if memory is >7 days old

**Frontmatter format:**
```yaml
---
name: Memory title
description: One-line description
type: user|feedback|project|reference
---

Memory content here.
```

#### karna/memory/prompts.py

`MEMORY_SYSTEM_PROMPT` -- comprehensive instructions injected into the system prompt covering:
- When to save (user asks, corrections, project context)
- What NOT to save (code patterns, git history, debugging solutions)
- Memory file format
- MEMORY.md index conventions
- Staleness verification rules

### karna/skills/

Skill system -- extend agent behavior with `.md` skill files.

#### karna/skills/loader.py

**`Skill` model:** name, description, instructions, triggers, file_path, enabled, version, author.

**`parse_skill_file(path)`:** Parse a `.md` file with YAML frontmatter into a `Skill` object.
- Minimal YAML parser (no PyYAML dependency)
- Supports lists, bools, quoted strings, multiline values

**`SkillManager`:**
- `load_all()` -- load all `.md` files from `~/.karna/skills/`
- `match_trigger(user_input)` -- find skills matching slash commands or keywords
- `get_skills_for_prompt()` -- build skills section within token budget
- `install_skill(source)` -- install from URL or local path
- `create_skill()` -- create a new skill file
- `enable_skill()` / `disable_skill()` -- toggle with frontmatter persistence

**How to write a skill:**
```markdown
---
name: my-skill
description: What this skill does
triggers: ["/my-skill", "do the thing"]
---

Instructions injected into the system prompt when active.
```

### karna/hooks/

Lifecycle hooks -- fire at well-known points in the agent loop.

#### karna/hooks/dispatcher.py

**`HookType` enum:** `PRE_TOOL_USE`, `POST_TOOL_USE`, `ON_ERROR`, `SESSION_START`, `SESSION_END`, `USER_PROMPT_SUBMIT`, `BEFORE_SEND`.

**`HookResult`:** `proceed` (bool), `modified_args` (optional), `message` (optional).

**`HookDispatcher`:**
- `register(hook_type, fn)` -- add a hook
- `dispatch(hook_type, **kwargs)` -- run all hooks sequentially, aggregate results
- If ANY hook returns `proceed=False`, the action is blocked
- `modified_args` chain through subsequent hooks
- Hooks loaded from `[[hooks]]` entries in config.toml (shell commands with placeholder substitution)

#### karna/hooks/builtins.py

Built-in hooks registered automatically:
- **Cost warning** (`PRE_TOOL_USE`): surfaces a message when session spend exceeds threshold ($1.00 default)
- **Git dirty warning** (`SESSION_START`): warns if working tree has uncommitted changes
- **Auto-save memory** (`POST_TOOL_USE`): scans responses for memory-worthy patterns (stub)

**How to add a new hook:**
1. Define an async function `async def my_hook(**kwargs) -> HookResult`.
2. Register via `dispatcher.register(HookType.XXX, my_hook)`.
3. Or add a shell command hook in `config.toml`:
```toml
[[hooks]]
type = "pre_tool_use"
command = "python ~/.karna/hooks/lint.py"
tools = ["edit", "write"]
```

### karna/permissions/

3-tier permission system for tool execution control.

#### karna/permissions/manager.py

**`PermissionLevel` enum:** `ALLOW` (auto-approve), `ASK` (prompt user), `DENY` (block).

**`PermissionRule`:** binds a tool name (or `*` wildcard) + optional regex pattern to a level.

**Built-in profiles:**
- `safe` -- read/grep/glob/web_search auto-allowed; bash/write/edit require approval
- `standard` -- same as safe but web_fetch also auto-allowed
- `yolo` -- everything auto-allowed

**`PermissionManager.check(tool_name, arguments)`:**
Evaluation order (first match wins):
1. Deny patterns (regex against serialized args)
2. Session allows (previously approved with "always")
3. Allow patterns (regex match)
4. Per-tool level from config
5. Wildcard `*` rule
6. Fallback: ASK

**`request_approval()`:** Prompts user for `y/N/always`. "always" grants session-scoped auto-approval.

### karna/compaction/

Context compaction -- summarize older messages to free tokens.

#### karna/compaction/compactor.py

`Compactor` -- auto-compact when estimated tokens exceed threshold (93% of context window).
- Preserves system prompt + last 5 messages
- Summarizes everything in between via the same provider
- Circuit breaker: stops after 3 consecutive failures
- Summary formatted as structured output (decisions, changes, open tasks)

#### karna/compaction/prompts.py

Templates for the summarization call:
- `COMPACT_SYSTEM_PROMPT` -- tells the model to produce ONLY summary text
- `SUMMARY_PROMPT` -- structured prompt requesting decisions, changes, tools used, open tasks

### karna/tokens/

Token counting with graceful fallback.

#### karna/tokens/counter.py

**`TokenCounter`:**
- Uses tiktoken when installed (`pip install karna[tokens]`)
- Selects encoding by model: `o200k_base` for GPT-4o/4.1/O3, `cl100k_base` for everything else
- Fallback: `len(text) // 4` (clearly documented as approximate)
- `count_messages()` adds 4-token overhead per message for structural framing

**`count_tokens(text, model="")`:** convenience function.

### karna/security/

Security primitives shared across the codebase.

#### karna/security/guards.py

- **`is_safe_path(path, allowed_roots)`** -- blocks sensitive paths (/etc/shadow, ~/.ssh, credentials), enforces cwd containment, handles path traversal via `..`
- **`scrub_secrets(text)`** -- regex-based removal of API keys (sk-, sk-or-v1-, sk-ant-), GitHub PATs, AWS access keys, PEM private keys, Bearer tokens, HuggingFace tokens
- **`is_safe_url(url)`** -- SSRF guard blocking private/reserved IPs, localhost, non-HTTP schemes
- **`check_dangerous_command(cmd)`** -- pattern matching for rm -rf /, dd, chmod 777 /, fork bombs, mkfs, curl|sh

#### karna/security/scrub.py

`scrub_for_memory(text)` -- extra scrubbing before writing to memory files:
1. Run `scrub_secrets()` for API keys
2. Redact file paths containing `credentials` or `.ssh`
3. Redact base64 blobs >100 chars (likely keys/certs)

### karna/tui/

Rich-based terminal interface.

#### karna/tui/repl.py

`run_repl(config, resume_conversation, resume_session_id)` -- main REPL loop.
- Prints startup banner
- Reads multiline input
- Dispatches slash commands
- Runs agent loop and streams output
- Persists messages to session database
- Displays per-turn cost info

#### karna/tui/output.py

`OutputRenderer` -- stateful renderer processing `StreamEvent` objects.
- Text deltas accumulated and rendered as Markdown
- Tool calls shown in yellow-bordered Rich panels with syntax-highlighted JSON
- Tool results in green-bordered panels (truncated to 2000 chars)
- Errors in red-bordered panels
- Usage info in dim text
- Spinner while waiting for first response

#### karna/tui/input.py

`get_multiline_input()` -- uses `prompt_toolkit` when available (readline, history, multiline via Esc+Enter), falls back to plain `input()`. Supports trailing backslash continuation.

#### karna/tui/slash.py

14 slash commands: `/help`, `/model`, `/clear`, `/history`, `/cost`, `/exit`, `/quit`, `/compact`, `/tools`, `/system`, `/sessions`, `/resume`, `/paste`, `/copy`.

**How to add a new slash command:**
1. Add a `SlashCommand` entry in `_build_commands()`.
2. Write a handler function `_cmd_mycommand(console, config, conversation, ...)`.
3. Register in the `_HANDLERS` dict.

#### karna/tui/banner.py

Startup banner -- version, active model, tool count, quick-help hint in a Rich panel.

#### karna/tui/themes.py

Color theme constants (Karna brand palette: `#3C73BD` blue, `#87CEEB` sky-blue) and Rich `Theme` object.

### karna/init.py

Project initialization for `nellie init`.
- `detect_project_type(cwd)` -- checks for pyproject.toml, package.json, Cargo.toml, go.mod
- `generate_karna_md_for_path()` -- creates KARNA.md with project-specific template (stack, conventions, agent defaults)
- Reads Python project metadata from pyproject.toml

### Stub Modules (Not Yet Implemented)

| Module | Phase | Purpose |
|---|---|---|
| `karna/backends/__init__.py` | Phase 3 | Backend abstraction layer |
| `karna/hooks/__init__.py` | Phase 3 | Hook system init (dispatcher lives in dispatcher.py) |
| `karna/compaction/__init__.py` | Phase 3 | Compaction init (compactor lives in compactor.py) |
| `karna/gateway/__init__.py` | Phase 4 | API gateway |
| `karna/server/__init__.py` | Phase 4 | Server mode |
| `karna/plugins/__init__.py` | Phase 4 | Plugin system |

---

## Extension Guide -- For Karna Engineers Adding Capabilities

### Adding a New Tool

1. Create `karna/tools/mytool.py`:
```python
from karna.tools.base import BaseTool
from typing import Any

class MyTool(BaseTool):
    name = "mytool"
    description = "What it does."
    sequential = False  # True if it mutates state
    parameters = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "..."},
        },
        "required": ["input"],
    }

    async def execute(self, **kwargs: Any) -> str:
        # Never raise -- capture errors and return as strings
        try:
            result = do_something(kwargs["input"])
            return result
        except Exception as exc:
            return f"[error] {exc}"
```
2. Register in `karna/tools/__init__.py`:
```python
_TOOL_PATHS["mytool"] = ("karna.tools.mytool", "MyTool")
```
3. Optionally add guidance in `karna/prompts/tool_descriptions.py`.

### Adding a New Provider

1. Create `karna/providers/myprovider.py` inheriting `BaseProvider`.
2. Implement `complete()`, `stream()`, `list_models()`.
3. Register in `karna/providers/__init__.py`.
4. Add pricing to `karna/models.py` `PRICING_TABLE` if applicable.

### Adding a New Hook

```python
from karna.hooks.dispatcher import HookResult, HookType

async def my_hook(**kwargs) -> HookResult:
    # Return HookResult(proceed=False) to block
    return HookResult(proceed=True, message="Hook fired!")

# Register:
dispatcher.register(HookType.PRE_TOOL_USE, my_hook)
```

Or via config.toml:
```toml
[[hooks]]
type = "pre_tool_use"
command = "echo {tool}"
tools = ["bash"]
```

### Adding a New Slash Command

1. Add entry in `karna/tui/slash.py` `_build_commands()`.
2. Write handler in same file.
3. Register in `_HANDLERS` dict.

### Adding a New Skill

Create `~/.karna/skills/my-skill.md`:
```markdown
---
name: my-skill
description: Short description
triggers: ["/my-skill", "keyword"]
---

Full instructions injected into the system prompt when triggered.
```

---

## Testing

### Structure

Tests live in `/tests/` and use pytest + pytest-asyncio.

| Test file | What it covers |
|---|---|
| `test_agent_loop.py` | Agent loop iteration, tool dispatch, error recovery |
| `test_cli.py` | CLI commands via typer.testing.CliRunner |
| `test_clipboard.py` | Clipboard tool platform detection |
| `test_config.py` | Config load/save, defaults |
| `test_context.py` | Context manager, project/git/env detection |
| `test_cost.py` | Cost computation, tracker, aggregation |
| `test_error_recovery.py` | Malformed JSON, loop detection, empty responses |
| `test_git_ops.py` | Git tool safety guards, operations |
| `test_image.py` | Image tool validation, marker parsing |
| `test_init.py` | Project type detection, KARNA.md generation |
| `test_mcp.py` | MCP connection, JSON-RPC protocol |
| `test_prompts.py` | System prompt building, model adaptations |
| `test_providers.py` | Provider serialization, usage extraction |
| `test_security.py` | Path safety, secret scrubbing, SSRF, dangerous commands |
| `test_sessions.py` | SessionDB CRUD, FTS5 search, resume |
| `test_tools.py` | BaseTool interface, tool registry |
| `test_tui.py` | Output renderer, slash commands |
| `test_web_tools.py` | Web search/fetch, SSRF guard |

### Running Tests

```bash
# All tests
pytest

# Single file
pytest tests/test_agent_loop.py

# Verbose with output
pytest -v -s

# With coverage (if installed)
pytest --cov=karna
```

### Writing a New Test

```python
import pytest
from karna.tools.mytool import MyTool

@pytest.mark.asyncio
async def test_mytool_basic():
    tool = MyTool()
    result = await tool.execute(input="test")
    assert "[error]" not in result

def test_mytool_error():
    tool = MyTool()
    # Sync wrapper for quick tests
    import asyncio
    result = asyncio.run(tool.execute(input=""))
    assert "[error]" in result
```

### Fixtures

Most tests use standard pytest fixtures. Common patterns:
- `tmp_path` for temporary file operations
- Mock providers with `AsyncMock` for testing the agent loop without API calls
- `SessionDB(db_path=tmp_path / "test.db")` for isolated database tests
