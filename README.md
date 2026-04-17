# Nellie

> **Internal Use Only -- Karna Engineering**

**Karna's internal AI agent harness for engineering teams. CLI binary: `nellie`.**

Nellie is a local-first, multi-provider AI agent framework built by Karna
for internal engineering use. It connects to LLM providers (OpenRouter,
OpenAI, Anthropic, Azure, Vertex AI, AWS Bedrock, local endpoints) and gives
the model access to tools (bash, file read/edit, grep, glob, web search,
git operations, MCP servers) so it can act as a capable coding and research
assistant. Works on Linux, macOS, and Windows.

## Quick Start

### Install

```bash
pip install git+https://github.com/Viraj0518/Karna-GenAI-CLI.git
```

Or for development:

```bash
git clone https://github.com/Viraj0518/Karna-GenAI-CLI.git
cd Karna-GenAI-CLI
pip install -e ".[dev]"
```

**Supported terminals:**
- Linux: any terminal (GNOME Terminal, kitty, alacritty, tmux, etc.)
- macOS: Terminal.app, iTerm2
- Windows: native `cmd.exe`, PowerShell, or Windows Terminal
- WSL: any Linux terminal
- **Not supported:** Git Bash / MSYS2 / Cygwin — prompt-toolkit can't attach to their console. Use one of the above.

### Configure

```bash
# Option A: env var (simplest)
export OPENROUTER_API_KEY="sk-or-v1-..."     # or ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.

# Option B: store encrypted credential (persists across sessions)
nellie auth login openrouter                  # prompts for API key
nellie auth login anthropic --key sk-ant-...  # or pass inline
nellie auth list                              # show configured providers
nellie auth logout openrouter                 # remove

# Set active model
nellie model set openrouter:meta-llama/llama-3.3-70b-instruct
nellie model set anthropic:claude-sonnet-4-5
nellie model set vertex:gemini-2.5-pro
nellie model set bedrock:anthropic.claude-sonnet-4

# View config
nellie config show
```

Configuration is stored in `~/.karna/config.toml`. Credentials live in
`~/.karna/credentials/` with 0600 permissions.

### Usage

```bash
# Show help
nellie --help

# Show version
nellie --version

# Enter the interactive REPL
nellie

# Show active model
nellie model

# Set model
nellie model set openrouter:anthropic/claude-sonnet-4

# Dump configuration
nellie config show
```

## Features

| Category | Details |
|----------|---------|
| **Multi-Provider** | OpenRouter, OpenAI, Anthropic, Azure OpenAI, Google Vertex AI, AWS Bedrock, local/Ollama endpoints, multi-credential failover |
| **Tool-Using Agent** | Iterative tool-call loop with bash, file I/O, grep, glob, web search/fetch, clipboard, git operations |
| **MCP Support** | Connect to Model Context Protocol servers for extended capabilities |
| **Local Sessions** | SQLite-backed session history stored in `~/.karna/sessions/` |
| **Cost Tracking** | Per-session and cumulative cost tracking with `nellie cost` |
| **Safety Guards** | Path traversal prevention, secret scrubbing, dangerous command detection, SSRF protection |
| **Context Management** | Long-context compaction, priority-ordered context injection, token budget awareness |
| **Skill System** | Extensible skill loader for custom behaviors |
| **Lifecycle Hooks** | Pre/post tool execution hooks for customization |
| **Credential Pooling** | Key rotation and multi-key support for high-volume usage |
| **Streaming** | Real-time token streaming with rate-limit handling and exponential backoff retry |

## Architecture

```
karna/
  cli.py          -- Typer CLI entry point (nellie)
  config.py       -- Pydantic config, loads ~/.karna/config.toml
  models.py       -- Message, ToolCall, ToolResult, Conversation
  providers/      -- LLM provider backends (OpenRouter, OpenAI, etc.)
  tools/          -- Agent tools (bash, read, edit, grep, glob)
  auth/           -- Credential management
  agents/         -- Agent loop
  sessions/       -- Session persistence
  tui/            -- Terminal UI
  memory/         -- Context/memory management
  skills/         -- Skill system
  hooks/          -- Lifecycle hooks
  compaction/     -- Context compaction
  plugins/        -- Third-party plugin loader (discover/load/activate)
```

## Roadmap

Nellie today is CLI + TUI only. The following are **not** implemented and
are tracked as future work:

- HTTP/API gateway for remote agent access
- Long-lived daemon / server mode with multi-client sessions
- Pluggable backend abstraction for provider connection pooling

## License

See [license.md](license.md) for usage terms.

Third-party attributions in [NOTICES.md](NOTICES.md).
