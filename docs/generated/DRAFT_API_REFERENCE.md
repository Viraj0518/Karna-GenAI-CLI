> **DRAFT -- auto-generated, not manually verified.** Produced by an AI
> documentation pass. Commands and signatures below have not been re-validated
> against the current source. Use `nellie --help` and read `karna/cli.py` for
> authoritative CLI surface.

# Nellie API Reference -- Karna Engineering

> **Nellie** — Karna's internal AI agent harness. CLI binary: `nellie`.
>
> Auto-generated documentation -- verify against source

This document covers the complete API surface of Nellie version 0.1.0.

---

## Table of Contents

1. [CLI Commands](#1-cli-commands)
2. [Configuration File](#2-configuration-file)
3. [Environment Variables](#3-environment-variables)
4. [Providers](#4-providers)
5. [Tools](#5-tools)
6. [Python Embedding API](#6-python-embedding-api)
7. [Data Models](#7-data-models)

---

## 1. CLI Commands

All commands are invoked via the `nellie` binary installed by the package.

```
nellie [OPTIONS] [COMMAND] [ARGS]...
```

### Global Options

| Option | Type | Description |
|--------|------|-------------|
| `--version` | flag | Show installed version and exit |
| `--help` | flag | Show help message |

### 1.1 Root Command — Interactive REPL

```
nellie
```

Launches the interactive agent REPL. Requires an active provider and API key.

---

### 1.2 Authentication (`nellie auth`)

```
nellie auth [COMMAND]
```

#### `nellie auth login`

```
nellie auth login <provider>
```

Authenticate with a provider interactively. Stores credentials in `~/.karna/credentials/<provider>.token.json`.

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `provider` | string | Yes | Provider name (`openrouter`, `openai`, `anthropic`, `azure`, `local`) |

---

### 1.3 Model Selection (`nellie model`)

```
nellie model [COMMAND]
```

#### `nellie model` (show active)

```
nellie model
```

Prints the currently active model in format `<provider>/<model>`.

#### `nellie model set`

```
nellie model set <model>
```

Sets the active model.

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `model` | string | Yes | Model identifier. Format: `<provider>:<model>` or just `<model>` (uses active provider). Examples: `openrouter:anthropic/claude-sonnet-4-20250514`, `claude-sonnet-4` |

**Short aliases supported** (OpenRouter provider):

| Alias | Resolves To |
|-------|-------------|
| `gpt-oss-120b` | `openai/gpt-oss-120b` |
| `gpt-4o` | `openai/gpt-4o` |
| `gpt-4o-mini` | `openai/gpt-4o-mini` |
| `gpt-4.1` | `openai/gpt-4.1` |
| `gpt-4.1-mini` | `openai/gpt-4.1-mini` |
| `gpt-4.1-nano` | `openai/gpt-4.1-nano` |
| `o3` | `openai/o3` |
| `o3-mini` | `openai/o3-mini` |
| `claude-opus-4` | `anthropic/claude-opus-4-20250514` |
| `claude-sonnet-4` | `anthropic/claude-sonnet-4-20250514` |
| `claude-3.5-sonnet` | `anthropic/claude-3-5-sonnet-20241022` |
| `deepseek-chat` | `deepseek/deepseek-chat` |
| `deepseek-reasoner` | `deepseek/deepseek-reasoner` |
| `gemini-2.5-pro` | `google/gemini-2.5-pro` |
| `gemini-2.5-flash` | `google/gemini-2.5-flash` |
| `llama-3.3-70b` | `meta-llama/llama-3.3-70b-instruct` |

---

### 1.4 Configuration (`nellie config`)

```
nellie config [COMMAND]
```

#### `nellie config show`

```
nellie config show
```

Dumps the current configuration as TOML. Shows active model, provider, system prompt, and generation parameters.

---

### 1.5 Session History (`nellie history`)

```
nellie history [COMMAND]
```

#### `nellie history` (list)

```
nellie history
```

Lists all saved sessions with IDs, timestamps, and costs.

#### `nellie history show`

```
nellie history show <id>
```

Shows full details of a session by ID.

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `id` | integer | Yes | Session ID |

#### `nellie history delete`

```
nellie history delete <id>
```

Deletes a specific session.

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `id` | integer | Yes | Session ID |

---

### 1.6 Cost Tracking (`nellie cost`)

```
nellie cost [COMMAND]
```

#### `nellie cost` (summary)

```
nellie cost
```

Displays aggregated cost summary across all sessions.

#### `nellie cost breakdown`

```
nellie cost breakdown <session_id>
```

Shows per-model cost breakdown for a specific session.

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `session_id` | integer | Yes | Session ID |

---

### 1.7 MCP Server Management (`nellie mcp`)

```
nellie mcp [COMMAND]
```

Commands for managing Model Context Protocol (MCP) servers. Full subcommand list available via `nellie mcp --help`.

---

## 2. Configuration File

**Location:** `~/.karna/config.toml`

### 2.1 Configuration Schema

```toml
[Nellie]
active_model = "openrouter/auto"
active_provider = "openrouter"
system_prompt = "You are Nellie, Karna's AI assistant."
max_tokens = 4096
temperature = 0.7
safe_mode = false
```

### 2.2 Field Reference

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `active_model` | string | `"openrouter/auto"` | — | Currently active model identifier in `<provider>/<model>` format |
| `active_provider` | string | `"openrouter"` | — | Provider name for the active model |
| `system_prompt` | string | `"You are Nellie, Karna's AI assistant."` | — | Default system prompt sent with every conversation |
| `max_tokens` | integer | `4096` | `ge=1` | Maximum tokens for completion |
| `temperature` | float | `0.7` | `0.0 <= x <= 2.0` | Sampling temperature |
| `safe_mode` | boolean | `false` | — | When `true`, block dangerous bash commands instead of warning |

### 2.3 Directory Structure

```
~/.karna/
├── config.toml         # Main configuration file (mode 0644)
└── credentials/
    ├── openrouter.token.json
    ├── openai.token.json
    ├── anthropic.token.json
    ├── azure.token.json
    └── local.token.json
```

### 2.4 Credential File Format

Each provider stores credentials in `~/.karna/credentials/<provider>.token.json`:

```json
{
  "api_key": "sk-or-v1-..."
}
```

**Security:** The credentials directory is mode `0700` and individual files are mode `0600`.

---

## 3. Environment Variables

### 3.1 Provider API Keys

| Variable | Provider | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | OpenRouter | API key for OpenRouter |
| `OPENAI_API_KEY` | OpenAI | API key for OpenAI |
| `ANTHROPIC_API_KEY` | Anthropic | API key for Anthropic |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI | API key for Azure OpenAI |
| `OPENAI_API_KEY` | Local (via OpenAI compat) | API key for local endpoints |

### 3.2 Azure-Specific Variables

| Variable | Description |
|----------|-------------|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_DEPLOYMENT` | Azure deployment name |
| `AZURE_OPENAI_API_VERSION` | API version (default: `2024-02-01`) |

### 3.3 Local Provider Variables

| Variable | Description |
|----------|-------------|
| `LOCAL_API_BASE` | Base URL for local endpoint (default: `http://localhost:11434/v1`) |
| `LOCAL_API_KEY` | API key for local endpoint (default: `none`) |

### 3.4 Karna System Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KARNA_CONFIG_PATH` | `~/.karna/config.toml` | Override config file location |
| `KARNA_SESSION_DB` | `~/.karna/sessions/sessions.db` | Session database path |
| `KARNA_LOG_LEVEL` | `INFO` | Logging level |

---

## 4. Providers

### 4.1 Provider Registry

Providers are registered in `karna/providers/__init__.py`:

| Provider Name | Class | Module |
|---------------|-------|--------|
| `openrouter` | `OpenRouterProvider` | `karna.providers.openrouter` |
| `openai` | `OpenAIProvider` | `karna.providers.openai` |
| `azure` | `AzureOpenAIProvider` | `karna.providers.azure` |
| `anthropic` | `AnthropicProvider` | `karna.providers.anthropic` |
| `local` | `LocalProvider` | `karna.providers.local` |

### 4.2 OpenRouter Provider

**Class:** `karna.providers.openrouter.OpenRouterProvider`

**Default Base URL:** `https://openrouter.ai/api/v1`

**Authentication:**
- Primary: `~/.karna/credentials/openrouter.token.json` (field: `api_key`)
- Fallback: `OPENROUTER_API_KEY` environment variable

**Features:**
- Non-streaming and streaming chat completions
- Tool use (function calling, OpenAI-compatible format)
- Model listing via `/api/v1/models`
- Model aliases (short names → full IDs)
- Per-call cost tracking

**Retry Configuration:**
- Max retries: `3`
- Base delay: `2.0` seconds
- Max delay: `60.0` seconds
- Jitter ratio: `0.5`
- Retries on: HTTP 429, 500, 502, 503, 504

### 4.3 OpenAI Provider

**Class:** `karna.providers.openai.OpenAIProvider`

**Default Base URL:** `https://api.openai.com/v1`

**Authentication:**
- Primary: `~/.karna/credentials/openai.token.json` (field: `api_key`)
- Fallback: `OPENAI_API_KEY` environment variable

**Features:**
- Non-streaming and streaming chat completions
- Tool use (function calling)
- Model listing via `/models`

### 4.4 Azure OpenAI Provider

**Class:** `karna.providers.azure.AzureOpenAIProvider`

**Default Base URL:** Derived from `AZURE_OPENAI_ENDPOINT`

**Authentication:**
- API key from `~/.karna/credentials/azure.token.json` (field: `api_key`) or `AZURE_OPENAI_API_KEY`
- Azure AD token support (via `azure.identity`)

**Environment Variables:**

| Variable | Required | Default |
|----------|----------|---------|
| `AZURE_OPENAI_ENDPOINT` | Yes | — |
| `AZURE_OPENAI_DEPLOYMENT` | No | `gpt-4o` |
| `AZURE_OPENAI_API_VERSION` | No | `2024-02-01` |

**Features:**
- Chat completions (non-streaming and streaming)
- Tool use (function calling)
- Azure AD authentication

### 4.5 Anthropic Provider

**Class:** `karna.providers.anthropic.AnthropicProvider`

**Default Base URL:** `https://api.anthropic.com/v1`

**Authentication:**
- Primary: `~/.karna/credentials/anthropic.token.json` (field: `api_key`)
- Fallback: `ANTHROPIC_API_KEY` environment variable

**Headers:**
- `anthropic-version`: `2023-06-01`
- `x-api-key`: API key
- `anthropic-dangerous-direct-browser-access`: `true`

**Features:**
- Non-streaming and streaming completions
- Tool use (Anthropic tool format)
- Usage tracking

### 4.6 Local Provider

**Class:** `karna.providers.local.LocalProvider`

**Default Base URL:** `http://localhost:11434/v1` (Ollama-compatible)

**Authentication:**
- API key from `~/.karna/credentials/local.token.json` (field: `api_key`) or `LOCAL_API_KEY`
- Supports `"none"` for unauthenticated endpoints

**Environment Variables:**

| Variable | Required | Default |
|----------|----------|---------|
| `LOCAL_API_BASE` | No | `http://localhost:11434/v1` |
| `LOCAL_API_KEY` | No | `none` |

**Features:**
- Chat completions (non-streaming and streaming)
- Tool use support (if endpoint supports it)
- OpenAI-compatible API

### 4.7 Base Provider Interface

All providers inherit from `karna.providers.base.BaseProvider` and implement:

```python
class BaseProvider:
    name: str
    base_url: str

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = True,
    ) -> AsyncIterator[StreamEvent] | StreamEvent: ...

    async def list_models(self) -> list[ModelInfo]: ...

    async def close(self) -> None: ...
```

---

## 5. Tools

### 5.1 Tool Registry

Tools are registered in `karna/tools/__init__.py`:

| Tool Name | Class | Module |
|-----------|-------|--------|
| `bash` | `BashTool` | `karna.tools.bash` |
| `read` | `ReadTool` | `karna.tools.read` |
| `write` | `WriteTool` | `karna.tools.write` |
| `edit` | `EditTool` | `karna.tools.edit` |
| `grep` | `GrepTool` | `karna.tools.grep` |
| `glob` | `GlobTool` | `karna.tools.glob` |
| `web_search` | `WebSearchTool` | `karna.tools.web_search` |
| `web_fetch` | `WebFetchTool` | `karna.tools.web_fetch` |
| `clipboard` | `ClipboardTool` | `karna.tools.clipboard` |
| `image` | `ImageTool` | `karna.tools.image` |
| `git` | `GitTool` | `karna.tools.git_ops` |

### 5.2 Base Tool Interface

```python
class BaseTool:
    name: str
    description: str

    async def execute(self, **kwargs: Any) -> str: ...
```

### 5.3 Tool Details

#### Bash Tool

**Class:** `karna.tools.bash.BashTool`

**Name:** `bash`

**Description:** Execute shell commands in the terminal.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `command` | string | Yes | The shell command to execute |
| `working_directory` | string | No | Directory to execute from (default: current directory) |

**Returns:** JSON with fields:
- `stdout`: string — standard output
- `stderr`: string — standard error