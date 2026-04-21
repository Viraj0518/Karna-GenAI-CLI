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
| `nellie mcp add/list/remove/test/serve` | Manage MCP servers / run Nellie as an MCP server |
| `nellie acp serve` | Run Nellie as an ACP (Agent Client Protocol) server over stdio |
| `nellie serve [--host --port]` | Run Nellie as a REST + SSE server (requires `karna[rest]`) |
| `nellie run --recipe <path> [--param k=v ...] [--workspace DIR]` | Execute a YAML recipe end-to-end (requires pyyaml) |
| `nellie cron add/list/remove/enable/disable/tick/run/daemon/show` | Manage scheduled jobs |
| `nellie think <prompt>` | One-shot question without launching the REPL |
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

Independent agents with their own conversation context, tools, and optional git worktree isolation. Enhanced with completion callbacks, SendMessage for continuing agents, and worktree auto-cleanup.

**Key function:**
- `spawn_subagent()` -- canonical one-shot entrypoint. Spawns an agent, runs it to completion, and returns the final assistant content as a string. Supports optional git worktree isolation.

**Classes:**
- `SubAgent` -- a single background agent. Supports `run()` (blocking) and `run_in_background()` (returns `asyncio.Task`). Can create/cleanup git worktrees for filesystem isolation. Completion callbacks (`on_complete`) notify the parent agent. Message queuing for in-flight agents.
- `SubAgentManager` -- registry of spawned subagents. Provides `spawn()`, `get()`, `list_active()`, `list_all()`, `send_message()`.

**Worktree lifecycle:**
1. `git worktree add` creates an isolated working copy (serialized via `_WORKTREE_LOCK`)
2. Subagent runs with `cwd` set to the worktree
3. On completion: if no changes detected, worktree is auto-cleaned up; if changes exist, worktree is preserved for manual review

**SendMessage (E4):**
- `SubAgentManager.send_message(agent_id, message)` -- continue a completed or running agent with new instructions
- For completed agents: appends the message and re-runs
- For running agents: queues the message for delivery after current iteration

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

19 tools registered in [karna/tools/__init__.py](../karna/tools/__init__.py). A 20th file — [`voice.py`](../karna/tools/voice.py) — sits on disk but is deliberately unregistered; see the note at the bottom of this table.

| Tool | File | Sequential | Description |
|---|---|---|---|
| `bash` | [bash.py](../karna/tools/bash.py) | Yes | Shell command execution with cwd tracking, output truncation, dangerous-command detection |
| `read` | [read.py](../karna/tools/read.py) | No | File reading with line numbers, offset+limit, binary/image detection, path safety |
| `write` | [write.py](../karna/tools/write.py) | Yes | File creation/overwriting with auto-mkdir, path safety |
| `edit` | [edit.py](../karna/tools/edit.py) | Yes | Exact string replacement with uniqueness check, replace_all support |
| `grep` | [grep.py](../karna/tools/grep.py) | No | Regex content search via ripgrep (fallback: grep -rn), glob filters, context lines |
| `glob` | [glob.py](../karna/tools/glob.py) | No | File pattern matching via pathlib, git-aware (.gitignore), sorted by mtime |
| `web_search` | [web_search.py](../karna/tools/web_search.py) | No | Privacy-first search: DuckDuckGo (default), Brave Search, SearXNG cascade |
| `web_fetch` | [web_fetch.py](../karna/tools/web_fetch.py) | No | URL fetching with SSRF guard, robots.txt respect, trafilatura extraction |
| `clipboard` | [clipboard.py](../karna/tools/clipboard.py) | No | Cross-platform clipboard read/write (macOS/X11/Wayland/WSL) |
| `image` | [image.py](../karna/tools/image.py) | No | Image inclusion for vision models -- base64 encoding with marker protocol |
| `git` | [git_ops.py](../karna/tools/git_ops.py) | Yes | Structured git operations with safety guards (blocks force-push, reset --hard) |
| `monitor` | [monitor.py](../karna/tools/monitor.py) | No | Background process streaming -- each stdout line becomes a notification event via `TaskRegistry` |
| `notebook` | [notebook.py](../karna/tools/notebook.py) | Yes | Jupyter .ipynb read/edit/add/create; runs cells only via `jupyter nbconvert` / `papermill` subprocesses (refuses in-process evaluation) |
| `document` | [document.py](../karna/tools/document.py) | No | Read `.docx`, `.xlsx`, `.pdf`, `.pptx`; macro extensions rejected |
| `task` | [task.py](../karna/tools/task.py) | No | Spawn background subagents with optional git worktree isolation |
| `db` | [database.py](../karna/tools/database.py) | No | SQLite / PostgreSQL / MySQL connector — parameterised queries, DSN SSRF guard, credential scrubbing |
| `browser` | [browser.py](../karna/tools/browser.py) | Yes | Headless Chromium via Playwright — per-request SSRF guard via `page.route()` |
| `comms` | [comms.py](../karna/tools/comms.py) | No | Inter-agent inbox: send/check/read/reply — 1 MB body cap |
| `mcp` | [mcp.py](../karna/tools/mcp.py) | No | MCP server connection, tool discovery, and proxying via JSON-RPC over stdio |

**Unregistered file:** [`voice.py`](../karna/tools/voice.py) defines a `VoiceTool` for TTS/STT via `pyttsx3` + `SpeechRecognition`, with graceful fallback when deps are absent. Its docstring flags it as a deliberate "register me later" handoff — it is **not** listed in `_TOOL_PATHS`, so `get_all_tools()` won't return it. Wiring it in is a one-line change once someone signs off.

The shared [`TaskRegistry`](../karna/tools/task_registry.py) singleton tracks monitors, background bash, and subagents in one place and drains pending notifications into the conversation between agent-loop turns.

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

`ProjectContext` -- walks up from cwd looking for instruction files. Implements the hierarchical merge strategy (E8).

**Priority system:**

| Priority | File | Description |
|---|---|---|
| 1 | `{root}/KARNA.md` | Project-level Karna instructions (highest) |
| 2 | `{root}/.karna/KARNA.md` | Alternate project location |
| 3 | `~/.karna/KARNA.md` | Global personal preferences |
| 5 | `CLAUDE.md` | Nellie compatibility |
| 6 | `.karna/project.toml` | TOML-format project config |
| 7 | `.cursorrules` | Cursor compatibility |
| 8 | `.github/copilot-instructions.md` | Copilot compatibility |

- All matching files are loaded and merged (closest to cwd shadows files further up)
- Project-level KARNA.md overrides global; both are injected if present
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

Persistent file-based memory system with 4 typed entries and automatic extraction.

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

#### karna/memory/extractor.py

`MemoryExtractor` -- automatic memory extraction from user messages. Zero LLM cost (regex-only).

**Pattern categories (priority order):**
1. **Negative feedback** -- corrections ("don't use X", "that's wrong", "never do Y")
2. **Positive feedback** -- confirmations ("yes, exactly", "keep doing it that way")
3. **User profile** -- self-identification ("I'm a data engineer", "I prefer X")
4. **Project facts** -- conventions ("we use DuckDB", "our standard is...")
5. **References** -- URLs, external system pointers

**Deduplication:** Each candidate is checked against existing memories using word-overlap similarity (threshold: 60%). Duplicates are skipped.

**Rate limiting:** Minimum 5 turns between automatic saves to avoid noise. At most 1 memory saved per turn.

**Integration:** Called from the `auto_save_memory_hook` after each assistant response.

#### karna/memory/prompts.py

`MEMORY_SYSTEM_PROMPT` -- comprehensive instructions injected into the system prompt covering:
- When to save (user asks, corrections, project context)
- What NOT to save (code patterns, git history, debugging solutions)
- Memory file format
- MEMORY.md index conventions
- Staleness verification rules

**How to add a new memory type:**
1. Add the type to `MemoryType` enum in `karna/memory/types.py`.
2. Add extraction patterns in `karna/memory/extractor.py` (new `_MY_TYPE_PATTERNS` list).
3. Add a detection block in `MemoryExtractor._detect_candidates()`.
4. Update `MEMORY_SYSTEM_PROMPT` in `karna/memory/prompts.py` with guidance for the new type.

### karna/skills/

Skill system -- extend agent behavior with `.md` skill files. Skills are now fully wired: injected into system prompts and matched via triggers in the REPL.

#### karna/skills/loader.py

**`Skill` model:** name, description, instructions, triggers, file_path, enabled, version, author.

**`parse_skill_file(path)`:** Parse a `.md` file with YAML frontmatter into a `Skill` object.
- Minimal YAML parser (no PyYAML dependency)
- Supports lists, bools, quoted strings, multiline values

**`SkillManager`:**
- `load_all()` -- load all `.md` files from `~/.karna/skills/` (project) and `~/.config/karna/skills/` (global)
- `match_trigger(user_input)` -- find skills matching slash commands or keywords. Called by the REPL before agent loop dispatch.
- `get_skills_for_prompt()` -- build skills section within token budget for system prompt injection
- `install_skill(source)` -- install from URL or local path
- `create_skill()` -- create a new skill file
- `enable_skill()` / `disable_skill()` -- toggle with frontmatter persistence (persists across sessions)

**Trigger matching flow:**
1. REPL receives user input
2. `SkillManager.match_trigger()` checks against all enabled skills
3. If a match is found, skill instructions are injected into the system prompt for that turn
4. Agent loop runs with the augmented prompt

**How to write a skill:**
```markdown
---
name: my-skill
description: What this skill does
triggers: ["/my-skill", "do the thing"]
---

Instructions injected into the system prompt when active.
```

**`/skills` slash command:**
- `/skills` -- list all loaded skills with enabled/disabled status
- `/skills enable <name>` -- enable a disabled skill
- `/skills disable <name>` -- disable a skill without deleting the file

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

Context compaction -- auto-summarize older messages when context fills up.

#### karna/compaction/compactor.py

`Compactor` -- auto-compact when estimated tokens exceed 80% of the context window.
- Triggered automatically by the agent loop (no user action needed)
- Also available manually via `/compact` slash command
- Preserves system prompt + last 5 messages (the "tail")
- Summarizes everything in between via the same provider
- Circuit breaker: stops after 3 consecutive failures to avoid infinite retry loops
- Summary formatted as structured output (decisions, changes, open tasks)
- Secret scrubbing applied before the summarization call (leaked keys never leave the host)

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

Security primitives shared across the codebase. See [docs/SECURITY_HARDENING.md](SECURITY_HARDENING.md) for the full rundown of fixes that landed on this branch.

#### karna/security/guards.py

- **[`is_safe_path`](../karna/security/guards.py)** -- blocks sensitive paths (/etc/shadow, ~/.ssh, credentials), enforces cwd containment, handles path traversal via `..`
- **[`scrub_secrets`](../karna/security/guards.py)** -- regex-based removal of API keys (sk-, sk-or-v1-, sk-ant-), GitHub PATs, AWS access keys, PEM private keys, Bearer tokens, HuggingFace tokens
- **[`is_safe_url`](../karna/security/guards.py)** -- SSRF guard blocking private/reserved IPs, localhost, non-HTTP schemes
- **[`check_dangerous_command`](../karna/security/guards.py)** -- pattern matching for rm -rf /, dd, chmod 777 /, fork bombs, mkfs, curl|sh

#### karna/security/scrub.py

[`scrub_for_memory(text)`](../karna/security/scrub.py) -- extra scrubbing before writing to memory files:
1. Run `scrub_secrets()` for API keys
2. Redact file paths containing `credentials` or `.ssh`
3. Redact base64 blobs >100 chars (likely keys/certs)

#### New tool-level guards landed on `dev`

These sit on top of the shared primitives above — each one is called out because a misuse would bypass the generic guards:

- **Notebook refuses in-process cell evaluation** ([tools/notebook.py](../karna/tools/notebook.py)) — the `_run_subprocess_execution` helper requires `jupyter nbconvert` or `papermill` on `$PATH`; there is no fallback that executes cell source in the Nellie host interpreter.
- **Database: parameterised queries + DSN SSRF + credential scrubbing** ([tools/database.py](../karna/tools/database.py)) — the tool accepts a `params` array and never string-formats user values into SQL; the connection string hostname is validated via `is_safe_url`; exception messages are passed through `scrub_secrets()` before surfacing.
- **Browser: per-request SSRF via `page.route()`** ([tools/browser.py](../karna/tools/browser.py)) — every network request the headless page makes (including redirect targets and subresources) is re-validated against `is_safe_url`, closing the DNS-rebinding and redirect-to-metadata holes in the one-shot `is_safe_url` check at `navigate()` time.
- **Comms: 1 MB message body cap** ([tools/comms.py](../karna/tools/comms.py)) — `send` and `reply` reject bodies larger than `_MAX_MESSAGE_BYTES = 1_000_000` before touching disk, preventing an agent-authored size bomb from filling the inbox.

### karna/rest_server/ — REST + SSE wrapper

Goose-parity HTTP surface. `create_app()` builds the FastAPI app, `serve(host, port)` wraps `uvicorn.run`. Session state (conversation + per-session SSE queue) lives in `SessionManager`; the queue back-pressures by dropping the oldest event when full so a stalled client can't block the agent loop.

**Run it:**
```bash
pip install 'karna[rest]'
nellie serve --host 0.0.0.0 --port 3030
# OpenAPI doc: http://127.0.0.1:3030/docs
```

**Endpoint shape:** see [docs/PARITY_SUMMARY.md](PARITY_SUMMARY.md) for the contract or the `REST-API` wiki page for full examples with the SSE event envelope.

### karna/acp_server/ — Agent Client Protocol over stdio

JSON-RPC 2.0 stdio server for agent↔agent communication. Distinct from MCP (which is host↔extension tool-server); ACP is peer-to-peer so a client agent can open a session, stream prompts, and consume `session/update` notifications as we produce them.

**Methods:** `initialize`, `session/new`, `session/list`, `session/prompt`, `session/cancel`, `session/close`, `ping`, `shutdown`. Notifications emit `{session_id, kind: text|tool_call|tool_result|error|done|cancelled, ...}`.

**Connect to it** (from another Nellie, an IDE plugin, or a test harness):
```json
{"command": "nellie", "args": ["acp", "serve"]}
```

Windows-safe stdin read via executor — the ProactorEventLoop can't `connect_read_pipe(sys.stdin)`, so we hand the `readline()` to a thread.

### karna/recipes/ — YAML recipe engine

A declarative spec for one agent run: instructions + parameter schema + tool allowlist + model pin + optional schedule.

**Minimal recipe:**
```yaml
name: triage_ticket
description: Summarise a CDC ticket + recommend triage
parameters:
  - name: ticket_id
    type: string
    required: true
  - name: priority
    type: string
    default: normal
extensions: [db, web_fetch]       # tool allowlist (names from karna.tools._TOOL_PATHS)
model: openrouter:anthropic/claude-haiku-4.5
max_iterations: 20
instructions: |
  Triage ticket {{ ticket_id }} at {{ priority }} priority.
  Read it from the database, summarise in 3 bullets, flag if vaccine-safety-related.
```

**Run it:**
```bash
pip install 'karna[recipes]'
nellie run --recipe triage_ticket.yaml --param ticket_id=CDC-4021 --param priority=high
```

Jinja2 with `StrictUndefined` catches missing variables at render time; if Jinja2 isn't installed, the runner falls back to a simple `{{var}}` substitution (no filters / no conditionals). `sub_recipes:` is parsed but dispatch through the `task` tool is gamma's G1 (in flight).

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

Slash commands (defined in [`_build_commands`](../karna/tui/slash.py)): `/help`, `/model`, `/clear`, `/history`, `/cost`, `/exit`, `/quit`, `/compact`, `/tools`, `/skills`, `/memory`, `/loop`, `/plan`, `/do`, `/system`, `/sessions`, `/resume`, `/paste`, `/copy`, `/tasks`, `/comms`.

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

### karna/rag/ — Retrieval-Augmented Generation

Local knowledge base indexed into a vector store. Entry point: [karna/rag/store.py](../karna/rag/store.py) (`KnowledgeStore`).

- Two backends: ChromaDB when `pip install karna[rag]` is available, otherwise a zero-dependency JSON + cosine-similarity fallback.
- Two embedders: `TFIDFEmbedder` (pure-Python, zero network) and `SentenceTransformerEmbedder` ([embedder.py](../karna/rag/embedder.py), downloads ~384 MB model from Hugging Face on first use — note this in docs).
- Storage: `~/.karna/knowledge/` (chroma dir or `index.json` + `meta.json`).
- Chunker in [chunker.py](../karna/rag/chunker.py) produces overlapping chunks; context injection wired in [context.py](../karna/rag/context.py).

### karna/comms/ — Multi-agent Messaging

File-based inbox for agents to pass messages, backed by markdown files in `~/.karna/comms/inbox/{agent}/`. Entry point: [karna/comms/inbox.py](../karna/comms/inbox.py) (`AgentInbox`).

- Messages are `.md` files with YAML frontmatter (from/to/timestamp/subject/priority/thread-id) — see [message.py](../karna/comms/message.py).
- Exposed to the model as the [`comms` tool](../karna/tools/comms.py) with `send`/`check`/`read`/`reply` actions; 1 MB body cap.
- `/comms` slash command in the TUI for manual inspection.

### karna/cron/ — Recurring Agent Jobs

Cron scheduler that runs agent prompts on a recurring schedule. Entry point: [karna/cron/scheduler.py](../karna/cron/scheduler.py) (`CronScheduler`).

- Storage: TOML at `~/.karna/cron/jobs.toml` ([store.py](../karna/cron/store.py)) with a YAML mirror ([jobs.py](../karna/cron/jobs.py)).
- Standard 5-field cron + `@daily`/`@hourly` aliases ([expression.py](../karna/cron/expression.py)).
- Two run modes: `nellie cron tick` (one-shot; wrap in OS cron) or `nellie cron daemon` ([daemon.py](../karna/cron/daemon.py), long-lived polling loop).
- Executor in [runner.py](../karna/cron/runner.py) feeds prompts through the normal agent loop.

### templates/ — Persona Templates

Shipped KARNA.md persona templates used by `nellie init` and referenced in docs:

- [templates/KARNA.md](../templates/KARNA.md) — generic
- [templates/KARNA-engineering.md](../templates/KARNA-engineering.md) — engineering
- [templates/KARNA-research.md](../templates/KARNA-research.md) — research
- [templates/KARNA-data-science.md](../templates/KARNA-data-science.md) — data science
- [templates/KARNA-bd.md](../templates/KARNA-bd.md) — business development
- [templates/KARNA-health-comms.md](../templates/KARNA-health-comms.md) — health comms

### Extension Point: Plugin Loader

| Module | Status | Purpose |
|---|---|---|
| `karna/plugins/__init__.py` | Implemented | Public exports for the plugin system |
| `karna/plugins/loader.py` | Implemented | Discovers `~/.karna/plugins/*/plugin.toml`, loads entry callables, activates them with a `KarnaContext` exposing `add_tool`/`add_skill`/`add_hook`/`add_command`. Wiring into `cli.py` startup is pending (TODO in `loader.py`). |

### Removed Stubs

The following empty stubs were deleted as dead code:

- `karna/backends/` — backend abstraction layer (no consumers, tracked as future work)
- `karna/gateway/` — HTTP/API gateway (tracked as future work)
- `karna/server/` — remote daemon/server mode (tracked as future work)

See the `## Roadmap` section of the repo `README.md` for the current future-work list.

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

### Adding a New Memory Type

1. Add the type to `MemoryType` enum in `karna/memory/types.py`:
```python
class MemoryType(str, Enum):
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"
    MY_TYPE = "my_type"  # new
```

2. Add extraction patterns in `karna/memory/extractor.py`:
```python
_MY_TYPE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bsome pattern\b", re.I),
]
```

3. Add a detection block in `MemoryExtractor._detect_candidates()`:
```python
for pattern in _MY_TYPE_PATTERNS:
    match = pattern.search(text)
    if match:
        snippet = self._extract_snippet(text, match)
        candidates.append(ExtractionCandidate(
            name=self._make_name("my_type", snippet),
            description=f"My type: {snippet[:80]}",
            type="my_type",
            content=f"My type fact: {snippet}",
        ))
        break
```

4. Update `MEMORY_SYSTEM_PROMPT` in `karna/memory/prompts.py` with guidance for the new type.

### Adding a New Subagent Type

The task tool supports three subagent types (`general`, `research`, `code`). To add a new type:

1. Add the type name to `_VALID_SUBAGENT_TYPES` in `karna/tools/task.py`.
2. Add a tool-filtering branch in `_filter_tools_for_type()`.
3. Update the `subagent_type` parameter's `enum` list in `TaskTool.parameters`.

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
