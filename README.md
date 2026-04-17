# Nellie

> **Internal Use Only -- Karna Engineering**

**Karna's internal AI agent harness for engineering teams. CLI binary: `nellie`.**

Nellie is a local-first, multi-provider AI agent framework built by Karna
for internal engineering use. It connects to LLM providers (OpenRouter,
OpenAI, Anthropic, Azure, local endpoints) and gives the model access to
tools (bash, file read/edit, grep, glob, web search, git operations) so it
can act as a capable coding and research assistant.

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

### Configure

```bash
# Set your API key (OpenRouter is the default provider)
export OPENROUTER_API_KEY="sk-or-v1-..."

# Set active model
nellie model set openrouter:meta-llama/llama-3.3-70b-instruct

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
| **Multi-Provider** | OpenRouter, OpenAI, Anthropic, Azure OpenAI, local/Ollama endpoints |
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
  gateway/        -- API gateway (planned)
  plugins/        -- Plugin system (planned)
  server/         -- Server mode (planned)
```

## License

See [license.md](license.md) for usage terms.

Third-party attributions in [NOTICES.md](NOTICES.md).
