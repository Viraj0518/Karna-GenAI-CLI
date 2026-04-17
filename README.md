# Karna

**Personal-use AI agent harness. CLI binary: `nellie`.**

Karna is a local-first, multi-provider AI agent framework designed for
personal productivity. It connects to LLM providers (OpenRouter, OpenAI,
Anthropic, Azure, local endpoints) and gives the model access to tools
(bash, file read/edit, grep, glob) so it can act as a capable coding and
research assistant.

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

# Or save it permanently
nellie auth login openrouter  # (interactive — coming in Phase 2)

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

# Show active model
nellie model

# Set model
nellie model set openrouter:anthropic/claude-sonnet-4

# Dump configuration
nellie config show
```

## Architecture

```
karna/
  cli.py          — Typer CLI entry point (nellie)
  config.py       — Pydantic config, loads ~/.karna/config.toml
  models.py       — Message, ToolCall, ToolResult, Conversation
  providers/      — LLM provider backends (OpenRouter, OpenAI, etc.)
  tools/          — Agent tools (bash, read, edit, grep, glob)
  auth/           — Credential management
  agents/         — Agent loop (Phase 2)
  sessions/       — Session persistence (Phase 2)
  tui/            — Terminal UI (Phase 2)
  memory/         — Context/memory management (Phase 3)
  skills/         — Skill system (Phase 3)
  hooks/          — Lifecycle hooks (Phase 3)
  compaction/     — Context compaction (Phase 3)
  gateway/        — API gateway (Phase 4)
  plugins/        — Plugin system (Phase 4)
  server/         — Server mode (Phase 4)
```

## License

MIT. See [LICENSE](LICENSE) for details.

Third-party attributions in [NOTICES.md](NOTICES.md).
