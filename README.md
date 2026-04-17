# Nellie

> **Internal Use Only — Karna Engineering**

Nellie is Karna's AI coding agent. It runs in your terminal, connects to any LLM, reads your codebase, writes code, runs commands, manages git, and learns your preferences across sessions. Zero telemetry. Runs locally. Costs pennies.

**→ New here? Read [GETTING_STARTED.md](GETTING_STARTED.md) first.**

---

## Install

```bash
git clone https://github.com/Viraj0518/Karna-GenAI-CLI.git
cd Karna-GenAI-CLI
pip install -e .
```

## Configure

```bash
# Get a free API key at openrouter.ai — gives you 200+ models
export OPENROUTER_API_KEY="sk-or-v1-..."

# Set the best free coding model
nellie model set openrouter:qwen/qwen3-coder
```

## Run

```bash
cd your-project/
nellie
```

---

## Why Nellie?

- **Any model** — OpenRouter (200+ models), Anthropic, OpenAI, Azure OpenAI, Google Vertex AI, AWS Bedrock, local/Ollama, multi-credential failover
- **15 tools** — bash, read, write, edit, grep, glob, git, web search, web fetch, clipboard, image, notebook, monitor, task, MCP
- **Parallel tool execution** — independent reads/greps run concurrently; writes are serialized for safety
- **Zero telemetry** — nothing phones home, ever
- **Session persistence** — SQLite-backed with full-text search, resume any past conversation
- **Cost tracking** — per-session and cumulative, with budget alerts
- **Memory system** — learns your preferences across sessions (user, feedback, project, reference types)
- **Skills** — extend Nellie with `.md` skill files for custom workflows
- **Hooks** — pre/post tool execution lifecycle events
- **3-tier permissions** — ALLOW/ASK/DENY per tool with pattern matching
- **Security** — path traversal guard, SSRF protection, secret scrubbing, dangerous command detection
- **Context compaction** — auto-summarizes old messages when context fills up
- **Credential pooling** — multi-key rotation for high-volume usage
- **Prompt caching** — Anthropic cache_control headers (10% cost on cache hits)

---

## Free Models (Recommended Starting Points)

No credit card needed. Fund $5 on [openrouter.ai](https://openrouter.ai) when ready for premium models.

| Model | Context | Best for | Command |
|---|---|---|---|
| **Qwen3 Coder 480B** ⭐ | 262K | Best free coding model — start here | `nellie model set openrouter:qwen/qwen3-coder` |
| **Qwen3 Next 80B** | 262K | General reasoning | `nellie model set openrouter:qwen/qwen3-next-80b` |
| **GPT-OSS 120B** | 131K | OpenAI's open-weight model | `nellie model set openrouter:openai/gpt-oss-120b` |
| **Nemotron-3 Super 120B** | 262K | NVIDIA, great for large files | `nellie model set openrouter:nvidia/nemotron-3-super-120b` |
| **DeepSeek R1** | 164K | Deep reasoning | `nellie model set openrouter:deepseek/deepseek-r1:free` |
| **Gemma 4 27B** | 262K | Google, supports images | `nellie model set openrouter:google/gemma-4-27b-it:free` |
| **Llama 3.3 70B** | 66K | Proven GPT-4-level workhorse | `nellie model set openrouter:meta-llama/llama-3.3-70b-instruct` |

---

## Commands

```bash
nellie                    # Start the interactive REPL
nellie init               # Initialize project (creates KARNA.md)
nellie model set <model>  # Set active model
nellie config show        # Show configuration
nellie cost               # Show spend summary
nellie history search <q> # Search past sessions
nellie resume             # Resume last session
nellie auth login <prov>  # Configure provider credentials
nellie mcp add <name>     # Add an MCP server
nellie --version          # Show version
```

### Slash commands (inside REPL)

`/help` · `/model` · `/cost` · `/compact` · `/history` · `/sessions` · `/resume` · `/tools` · `/copy` · `/paste` · `/clear` · `/exit`

---

## Project Setup

```bash
cd your-project/
nellie init
```

Creates `KARNA.md` — edit it to teach Nellie your project's stack, conventions, and boundaries:

```markdown
# KARNA.md

## Stack
Python 3.11, Polars, DuckDB, FastAPI

## Conventions
- Use polars over pandas for new code
- SQL queries in queries/ as .sql files
- Tests mirror source structure

## Don't touch
- config/prod/ (production secrets)
- migrations/ (use alembic)
```

---

## Architecture

```
CLI (nellie) → REPL (Rich TUI) → Agent Loop → Provider (LLM API)
                                      ↕
                               Tool Execution
                          (parallel when safe)
                                      ↕
                            Permission Check
                            Safety Guards
                            Hook Dispatch
```

```
karna/
├── cli.py              — Entry point, 14 commands
├── config.py           — KarnaConfig (TOML-backed)
├── models.py           — Message, ToolCall, Conversation, Usage
├── agents/
│   ├── loop.py         — Agent loop (streaming + sync, parallel tool dispatch)
│   ├── subagent.py     — Subagent spawning + message passing
│   └── safety.py       — Pre-tool-use safety checks
├── providers/
│   ├── openrouter.py   — OpenRouter (200+ models, streaming SSE)
│   ├── anthropic.py    — Anthropic (prompt caching, cache_control)
│   ├── openai.py       — OpenAI / Azure
│   ├── local.py        — Local endpoints (Ollama, vLLM, etc.)
│   └── caching.py      — Prompt cache helper
├── tools/              — 15 tools (bash, read, write, edit, grep, glob,
│                         git, web_search, web_fetch, clipboard, image,
│                         notebook, monitor, task, MCP)
├── auth/               — Credential store + multi-key pool rotation
├── context/            — Project detection (KARNA.md), git awareness
├── prompts/            — System prompt builder, per-model adaptations
├── sessions/           — SQLite FTS5 persistence + cost tracking
├── memory/             — 4-type memory system (user/feedback/project/reference)
├── skills/             — .md skill files → SkillManager
├── hooks/              — Lifecycle events (PreToolUse, PostToolUse, etc.)
├── permissions/        — 3-tier ALLOW/ASK/DENY per tool
├── compaction/         — Context summarization when window fills
├── tokens/             — Token counting (tiktoken or fallback)
├── security/           — Path traversal, SSRF, secret scrub, dangerous cmd
└── tui/                — Rich REPL, streaming, slash commands
```

---

## Documentation

| Doc | What |
|---|---|
| [GETTING_STARTED.md](GETTING_STARTED.md) | **Start here** — setup, models, best practices for the analytics team |
| [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) | Full architecture walkthrough, module reference, extension guide |
| [docs/CODEBASE_MAP.md](docs/CODEBASE_MAP.md) | One-liner per file (85 files) |
| [docs/DIFF_AUDIT.md](docs/DIFF_AUDIT.md) | Competitive comparison vs Claude Code, Cursor, etc. |

---

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -q          # 415 tests, ~15s
```

---

## License

See [license.md](license.md) for usage terms. Third-party attributions in [NOTICES.md](NOTICES.md).
