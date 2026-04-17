```markdown
# Karna

**Personal-use AI agent harness. CLI binary: `nellie`.**

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)](https://pypi.org/project/karna/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Karna is a local-first, multi-provider AI agent framework designed for personal
productivity. It connects to LLM providers (OpenRouter, OpenAI, Anthropic,
Azure, local endpoints) and gives the model access to tools (bash, file
read/edit, grep, glob, web search, and more) so it can act as a capable coding
and research assistant.

Everything runs locally. Your conversations never leave your machine by
default, and credentials are stored with restrictive file permissions.

---

## ✨ Features

| Category | Highlights |
|----------|------------|
| **Multi-Provider** | OpenRouter, OpenAI, Anthropic, Azure OpenAI, and local/Ollama endpoints |
| **Tool-Using Agent** | Iterative tool-call loop with bash, file I/O, grep, glob, web search/fetch, clipboard, and Git operations |
| **MCP Support** | Connect to Model Context Protocol servers for extended capabilities |
| **Local Sessions** | SQLite-backed session history stored in `~/.karna/sessions/` — never sent externally |
| **Cost Tracking** | Per-session and cumulative cost tracking with `nellie cost` commands |
| **Safety Guards** | Path traversal prevention, secret scrubbing, dangerous command detection, SSRF protection |
| **Context Management** | Long-context compaction, priority-ordered context injection, token budget awareness |
| **Skill System** | Extensible skill loader for custom behaviors |
| **Lifecycle Hooks** | Pre/post tool execution hooks for customization |
| **Credential Pooling** | Key rotation and multi-key support for high-volume usage |
| **Streaming** | Real-time token streaming with rate-limit handling and exponential backoff retry |
| **Configurable** | TOML-based configuration at `~/.karna/config.toml` |

---

## 📦 Installation

### Prerequisites

- Python 3.10 or higher
- A compatible LLM provider account (see [Provider Support](#-provider-support))

### Stable Release (via pip)

```bash
pip install git+https://github.com/Viraj0518/Karna-GenAI-CLI.git
```

### Development Installation

```bash
git clone https://github.com/Viraj0518/Karna-GenAI-CLI.git
cd Karna-GenAI-CLI
pip install -e ".[dev]"
```

This installs Karna with development dependencies (pytest, ruff, pre-commit).

### Optional Extras

```bash
# Token counting support (recommended for accurate cost tracking)
pip install karna[tokens]

# Web fetching and scraping capabilities
pip install karna[web]

# Both extras
pip install karna[tokens,web]
```

---

## 🚀 Quickstart

### 1. Authenticate

```bash
# Set your API key via environment variable (quickest option)
export OPENROUTER_API_KEY="sk-or-v1-your-key-here"

# Or authenticate interactively
nellie auth login openrouter
```

### 2. Configure Your Model

```bash
# Use a short alias (resolved automatically)
nellie model set claude-sonnet-4

# Or specify full provider:model path
nellie model set openrouter:anthropic/claude-opus-4-20250514

# View available models
nellie model list
```

### 3. Start the Agent

```bash
# Enter the interactive REPL
nellie

# Or pass a task directly
nellie "Explain what this script does: ./analyze.py"
```

### 4. Check Your Configuration

```bash
nellie config show
```

---

## ⚙️ Configuration

Karna stores configuration in `~/.karna/config.toml`. On first run, it creates
the directory structure with secure permissions:

```
~/.karna/
├── config.toml          # Main configuration (mode 0644)
├── credentials/         # API keys (mode 0700, files mode 0600)
└── sessions/            # SQLite session history
```

### Configuration Options

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `active_model` | string | `openrouter/auto` | Active model identifier (`provider/model`) |
| `active_provider` | string | `openrouter` | Default provider name |
| `system_prompt` | string | `You are Karna, a helpful AI assistant.` | Default system prompt |
| `max_tokens` | int | `4096` | Maximum tokens per completion |
| `temperature` | float | `0.7` | Sampling temperature (0.0–2.0) |
| `safe_mode` | bool | `false` | Block dangerous bash commands instead of warning |

### Example `config.toml`

```toml
[app]
active_model = "openrouter/anthropic/claude-sonnet-4-20250514"
active_provider = "openrouter"
system_prompt = "You are Karna, a helpful AI assistant focused on code quality."
max_tokens = 8192
temperature = 0.7
safe_mode = false
```

### Environment Variable Precedence

Environment variables override `config.toml` values:

| Variable | Config Field |
|----------|-------------|
| `OPENROUTER_API_KEY` | — |
| `OPENAI_API_KEY` | — |
| `ANTHROPIC_API_KEY` | — |
| `AZURE_OPENAI_API_KEY` | — |
| `OPENAI_BASE_URL` | Azure / local endpoints |

---

## 📋 Available Commands

### Core Commands

```bash
nellie                          # Enter interactive REPL
nellie --version                # Show version
nellie --help                    # Show help
```

### Authentication

```bash
nellie auth login <provider>     # Authenticate with a provider (interactive)
nellie auth list                 # List saved credentials
nellie auth delete <provider>    # Remove stored credentials
```

### Model Management

```bash
nellie model                    # Show current model
nellie model list               # List available models for current provider
nellie model set <model>         # Set active model (supports short aliases)
```

### Configuration

```bash
nellie config show              # Display current configuration
nellie config set <key> <value> # Update a config value
nellie config reset             # Reset to defaults
```

### Session History

```bash
nellie history                  # List recent sessions
nellie history show <id>       # Show a specific session
nellie history delete <id>      # Delete a session
nellie history clear            # Clear all session history
```

### Cost Tracking

```bash
nellie cost                     # Show cost summary
nellie cost session <id>        # Show cost for a specific session
nellie cost reset               # Reset cost counters
```

### MCP Server Management

```bash
nellie mcp list                 # List configured MCP servers
nellie mcp add <server>         # Add an MCP server
nellie mcp remove <server>      # Remove an MCP server
```

---

## 🔌 Provider Support

Karna supports multiple LLM providers out of the box:

| Provider | ID | Features | Notes |
|----------|----|----------|-------|
| **OpenRouter** | `openrouter` | Tool use, streaming, cost tracking | Default provider. Best for accessing multiple models. |
| **OpenAI** | `openai` | Tool use, streaming | Direct API access. |
| **Anthropic** | `anthropic` | Tool use, streaming | Direct API access to Claude models. |
| **Azure OpenAI** | `azure` | Tool use, streaming | Azure-hosted endpoints. |
| **Local** | `local` | Basic chat | Ollama and compatible local endpoints. |

### Model Aliases

Short aliases for common models are supported when using OpenRouter:

| Alias | Resolves To |
|-------|-------------|
| `claude-sonnet-4` | `anthropic/claude-sonnet-4-20250514` |
| `claude-opus-4` | `anthropic/claude-opus-4-20250514` |
| `claude-3.5-sonnet` | `anthropic/claude-3-5-sonnet-20241022` |
| `gpt-4o` | `openai/gpt-4o` |
| `gpt-4o-mini` | `openai/gpt-4o-mini` |
| `o3` | `openai/o3` |
| `o3-mini` | `openai/o3-mini` |
| `deepseek-chat` | `deepseek/deepseek-chat` |
| `deepseek-reasoner` | `deepseek/deepseek-reasoner` |
| `gemini-2.5-pro` | `google/gemini-2.5-pro` |
| `gemini-2.5-flash` | `google/gemini-2.5-flash` |
| `llama-3.3-70b` | `meta-llama/llama-3.3-70b-instruct` |

### Adding a New Provider

1. Create a new file `karna/providers/<name>.py`
2. Inherit from `BaseProvider`
3. Implement the required abstract methods
4. Register in `karna/providers/__init__.py`

---

## 🛠 Tool List

Karna ships with a set of built-in tools. Each tool is exposed to the LLM
with auto-generated documentation.

| Tool | Description | Key Functions |
|------|-------------|---------------|
| **bash** | Execute shell commands | `run(command)`, `kill(pid)` |
| **read** | Read file contents | `read_file(path)` |
| **write** | Write content to files | `write_file(path, content)` |
| **edit** | Apply targeted edits to files | `apply_edit(path, old, new)` |
| **grep** | Search file contents | `grep(pattern, path, options)` |
| **glob** | Find files by pattern | `glob(pattern, root)` |
| **web_search** | Search the web | `search(query)` |
| **web_fetch** | Fetch web pages | `fetch(url)` |
| **clipboard** | System clipboard access | `copy(text)`, `paste()` |
| **image** | Analyze images | `analyze(image_path)` |
| **git** | Git operations | `status`, `diff`, `log`, `commit`, `branch` |

### MCP Tools

Karna supports Model Context Protocol servers. Tools from connected MCP
servers are dynamically registered and available alongside built-in tools.

---

## 🔒 Security Model

Karna is designed with security as a first-class concern:

### Credential Security

- Credentials stored in `~/.karna/credentials/` with mode `0700`
- Individual credential files with mode `0600`
- Environment variables take precedence (useful for CI/CD)

### Path Safety

The following paths are blocked from all file operations:

- `/etc/shadow`, `/etc/passwd`, `/etc/sudoers`
- `~/.ssh/`
- `~/.karna/credentials/`
- `/dev/`, `/proc/`, `/sys/`
- Any path attempting to escape the working directory via `..`

### Network Security

- HTTPS enforced for all provider URLs (except localhost/127.0.0.1 for local endpoints)
- TLS certificate verification always enabled
- SSRF protection prevents access to internal network ranges
- Request/response bodies are **never logged** (they contain user conversations)

### Command Safety

- **safe_mode** blocks dangerous commands instead of warning
- Secret detection and scrubbing in tool outputs
- Dangerous bash patterns are flagged (e.g., `rm -rf /`, privilege escalation)

### Privacy

- All session data stored locally in `~/.karna/sessions/sessions.db`
- **No session data is ever sent to any external service**
- Sessions can be deleted individually or cleared entirely

### Security Checklist

- [ ] Keep `~/.karna/credentials/` permissions at `0700`
- [ ] Enable `safe_mode = true` for untrusted workflows
- [ ] Review tool outputs before approving sensitive operations
- [ ] Use environment variables for credentials in shared environments

---

## 🏗 Architecture

```
karna/
├── cli.py              # Typer CLI entry point (nellie command)
├── config.py           # Pydantic configuration, TOML persistence
├── models.py           # Core data models (Message, ToolCall, etc.)
├── auth/
│   ├── credentials.py   # Credential loading and storage
│   └── pool.py         # Credential pool with key rotation
├── providers/
│   ├── base.py         # Abstract BaseProvider (retry, rate limiting)
│   ├── openrouter.py   # OpenRouter implementation
│   ├── openai.py       # OpenAI implementation
│   ├── anthropic.py    # Anthropic implementation
│   ├── azure.py        # Azure OpenAI implementation
│   ├── local.py        # Local/Ollama implementation
│   └── caching.py      # Prompt caching support
├── tools/
│   ├── base.py         # Abstract BaseTool
│   ├── bash.py         # Shell command execution
│   ├── read.py         # File reading
│   ├── write.py        # File writing
│   ├── edit.py         # Targeted file editing
│   ├── grep.py         # Content search
│   ├── glob.py         # Pattern-based file finding
│   ├── web_search.py   # Web search
│   ├── web_fetch.py    # Web page fetching
│   ├── clipboard.py    # System clipboard
│   ├── image.py        # Image analysis
│   └── git_ops.py      # Git operations
├── agents/
│   ├── loop.py         # Main agent loop (tool-call cycle)
│   ├── safety.py       # Pre-tool execution checks
│   └── subagent.py     # Sub-agent orchestration
├── security/
│   ├── guards.py       # Path, SSRF, secret detection guards
│   └── scrub.py        # Secret scrubbing utilities
├── sessions/
│   ├── db.py           # SQLite session persistence
│   └── cost.py         # Cost tracking
├── prompts/
│   ├── system.py       # System prompt builder
│   └── tool_descriptions.py  # Auto-generated tool docs
├── memory/
│   ├── manager.py      # Context and memory management
│   ├── types.py        # Memory type definitions
│   └── prompts.py      # Memory-related prompts
├── compaction/
│   └── compactor.py    # Long-context summarization
├── skills/
│   └── loader.py       # Custom skill system loader
├── hooks/
│   ├── dispatcher.py   # Hook event dispatcher
│   └── builtins.py     # Built-in hook implementations
├── context/
│   ├── manager.py      # Context window management
│   ├── project.py      # Project context detection
│   ├── git.py          # Git context
│   └── environment.py  # Environment variables context
├── tokens/
│   └── counter.py      # Token counting utilities
└── server/
                        # (Extensible server component)
```

---

## 🤝 Contributing

Contributions are welcome! Please follow these guidelines:

### Getting Started

1. **Fork the repository** and clone locally:

```bash
git clone https://github.com/YOUR_USERNAME/Karna-GenAI-CLI.git
cd Karna-GenAI-CLI
pip install -e ".[dev]"
```

2. **Install pre-commit hooks**:

```bash
pre-commit install
```

3. **Run tests** before making changes:

```bash
pytest
```

### Code Style

- Python 3.10+ with type hints
- Line length: 120 characters
- Linting: `ruff` (E, F, I, W rules)
- Formatting: black-compatible with ruff

```bash
# Run linter
ruff check .

# Format code
ruff format .
```

### Adding a New Tool

1. Create `karna/tools/<name>.py` inheriting from `BaseTool`
2. Implement `name`, `description`, and `execute(**kwargs)` method
3. Register in `karna/tools/__init__.py` `_TOOL_PATHS` dict
4