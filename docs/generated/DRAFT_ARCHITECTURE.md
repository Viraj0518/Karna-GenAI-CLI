> **DRAFT -- auto-generated, not manually verified.** Produced by an AI
> documentation pass. The architecture described below may mention components
> that have been removed (e.g. `gateway/`, `backends/`, `server/` stubs were
> deleted) or features that are aspirational. Cross-check against the live
> module tree under `karna/` before relying on any detail here.

# Nellie Architecture -- Karna Engineering

> Auto-generated documentation -- verify against source

Nellie (CLI: `nellie`) is Karna's internal, local-first, multi-provider AI agent framework for engineering teams. It connects to LLM providers and gives models access to tools so they can act as capable coding and research assistants.

## System Overview

Nellie operates as an interactive agent loop: it receives user input, sends it to an LLM provider, and when the model requests tool execution, Nellie runs those tools locally and feeds the results back. This cycle repeats until the model produces a final response.

**Core design principles:**

- **Provider agnostic**: Any model supporting chat completions can be plugged in
- **Tool-based agency**: Models invoke tools rather than having open-ended bash access
- **Local-first**: All sessions, credentials, and context live on the user's machine
- **Security by default**: Path traversal guards, secret scrubbing, permission checks
- **Extensible**: Custom tools, hooks, skills, and MCP servers can be registered

---

## Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              CLI Layer                                   │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                         nellie (cli.py)                         │    │
│  │  auth | model | config | mcp | history | cost                   │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          Core Services                                   │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐   │
│  │   Config    │  │   Models    │  │   Providers  │  │    Tools    │   │
│  │  (config.py)│  │  (models.py)│  │  (providers/)│  │   (tools/)  │   │
│  └─────────────┘  └─────────────┘  └──────────────┘  └─────────────┘   │
│        │                │                │                │            │
│        └────────────────┬┴────────────────┴────────────────┘            │
│                         ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                       Agent Loop (agents/loop.py)                │    │
│  │   User Input → Provider → Model → Tool Calls → Execute → Loop   │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
          ┌─────────────────────────┼─────────────────────────┐
          ▼                         ▼                         ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   Auth Layer    │  │   Session DB    │  │   Security      │
│  (auth/credentials)│  │ (sessions/db.py)│  │ (security/)     │
│  ~/.karna/creds │  │ ~/.karna/sessions│  │ Guards + Scrub  │
└─────────────────┘  └─────────────────┘  └─────────────────┘
          │                         │                 │
          ▼                         ▼                 ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   Context       │  │    Memory      │  │    Hooks        │
│ (context/)      │  │  (memory/)     │  │  (hooks/)       │
│ env, git, proj  │  │  manager.py    │  │  dispatcher.py  │
└─────────────────┘  └─────────────────┘  └─────────────────┘
          │                         │                 │
          └─────────────────────────┼─────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        Extension Points                                  │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────────┐       │
│  │  Skills   │  │    MCP    │  │  Plugins  │  │    Skills     │       │
│  │ (skills/) │  │  Server   │  │(plugins/) │  │   Loader      │       │
│  └───────────┘  └───────────┘  └───────────┘  └───────────────┘       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Provider Abstraction

Providers handle all communication with LLM backends. The abstraction is defined in `karna/providers/base.py`.

### BaseProvider Interface

```python
class BaseProvider(ABC):
    name: str
    base_url: str
    
    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        model: str | None = None,
        stream: bool = True,
        **kwargs
    ) -> AsyncIterator[StreamEvent]: ...
    
    @abstractmethod
    async def list_models(self) -> list[ModelInfo]: ...
```

### Available Providers

| Provider | Module | Notes |
|----------|--------|-------|
| OpenRouter | `providers/openrouter.py` | Default provider; routes to multiple backends |
| OpenAI | `providers/openai.py` | Direct OpenAI API access |
| Anthropic | `providers/anthropic.py` | Anthropic Claude API |
| Azure | `providers/azure.py` | Azure OpenAI endpoints |
| Local | `providers/local.py` | Self-hosted endpoints |

### Provider Resolution

```python
# From karna/providers/__init__.py
from karna.providers import get_provider

# Model identifiers: "<provider>/<model>"
provider = get_provider("openrouter")  # Returns instantiated OpenRouterProvider
provider = get_provider("openai", api_key="sk-...")  # With kwargs
```

Model identifiers use the format `<provider>/<model>`. For example:

- `openrouter/anthropic/claude-sonnet-4-20250514`
- `openai/gpt-4o`
- `local/llama-3.3-70b`

OpenRouter supports model aliases for convenience:

```python
MODEL_ALIASES = {
    "claude-sonnet-4": "anthropic/claude-sonnet-4-20250514",
    "deepseek-chat": "deepseek/deepseek-chat",
    "gemini-2.5-pro": "google/gemini-2.5-pro",
}
```

### Common Provider Features

**Credential Loading** (`providers/base.py`):

- API keys loaded from `~/.karna/credentials/<provider>.token.json` (field: `api_key`)
- Environment variable fallback: `$OPENROUTER_API_KEY`, `$OPENAI_API_KEY`, etc.
- Credentials directory is mode `0700`; individual files are `0600`

**Retry Logic**:

- Jittered exponential backoff for retries
- Configurable: `max_retries=3`, `base_delay=2.0s`, `max_delay=60.0s`
- Auto-retries on 429 (rate limit), 500, 502, 503, 504
- Respects `Retry-After` header from rate-limited responses

**Security Invariants**:

- HTTPS enforced for all provider URLs (except `localhost` / `127.0.0.1`)
- TLS verification always enabled
- Request/response bodies never logged (contain user conversations)
- Only metadata logged: model name, token count, latency, cost

**Cost Tracking** (`providers/base.py`):

```python
usage = await provider.chat(messages, tools=tools)
print(f"Input tokens: {usage.input_tokens}, Output: {usage.output_tokens}")
print(f"Estimated cost: ${usage.cost}")
```

---

## Tool System

Tools are the primary mechanism through which the agent interacts with the system.

### Tool Registry

```python
# From karna/tools/__init__.py
_TOOL_PATHS = {
    "bash": ("karna.tools.bash", "BashTool"),
    "read": ("karna.tools.read", "ReadTool"),
    "write": ("karna.tools.write", "WriteTool"),
    "edit": ("karna.tools.edit", "EditTool"),
    "grep": ("karna.tools.grep", "GrepTool"),
    "glob": ("karna.tools.glob", "GlobTool"),
    "web_search": ("karna.tools.web_search", "WebSearchTool"),
    "web_fetch": ("karna.tools.web_fetch", "WebFetchTool"),
    "clipboard": ("karna.tools.clipboard", "ClipboardTool"),
    "image": ("karna.tools.image", "ImageTool"),
    "git": ("karna.tools.git_ops", "GitTool"),
}

def get_tool(name: str) -> BaseTool: ...
def get_all_tools() -> list[BaseTool]: ...
```

### BaseTool Interface

```python
# From karna/tools/base.py
class BaseTool(ABC):
    name: str
    description: str
    parameters: dict  # JSON Schema for tool parameters
    
    @abstractmethod
    async def execute(self, **kwargs) -> str: ...
```

### Tool Execution Flow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  ToolCall    │────▶│  ToolRunner  │────▶│  ToolResult  │
│  {id, name,  │     │  (agent loop) │     │  {tool_call  │
│   arguments} │     │              │     │   _id, content│
└──────────────┘     └──────┬───────┘     │   is_error}  │
                           │              └──────────────┘
                           ▼
                    ┌──────────────┐
                    │   Safety     │
                    │   Check      │
                    │(pre_tool_    │
                    │  check)      │
                    └──────────────┘
```

### Built-in Tools

| Tool | Purpose | Key Features |
|------|---------|--------------|
| `bash` | Shell commands | Blocked commands in safe mode; secret detection |
| `read` | Read file contents | Path validation; line ranges |
| `write` | Create/overwrite files | Atomic writes; directory creation |
| `edit` | Patch file sections | Regex or line-based edits |
| `grep` | Search file contents | Pattern matching; context lines |
| `glob` | Find files by pattern | Recursive search; exclusions |
| `web_search` | Search the web | API-based search |
| `web_fetch` | Download web pages | SSRF protection; content extraction |
| `clipboard` | System clipboard | Read/write clipboard |
| `git` | Git operations | Status, diff, commit, branch |

---

## Agent Loop

The agent loop (`karna/agents/loop.py`) is the core execution engine.

### Loop Algorithm

```
1. Build system prompt from:
   - Identity (Nellie persona)
   - Available tools (auto-generated from tool registry)
   - Behavioral guidelines
   - Context (git status, current directory, etc.)

2. Send messages to provider.chat()

3. For each streaming event:
   - Accumulate content
   - Collect tool calls when detected

4. For each ToolCall:
   a. pre_tool_check(tool_call)  → Safety validation
   b. Execute tool via tool.execute(**tool_call.arguments)
   c. Collect ToolResult

5. Append assistant message (with tool_calls) to history
6. Append tool results to history
7. GOTO step 2 (until no more tool calls)

8. Return final assistant message to user
```

### Tool Execution with Safety

```python
# From karna/agents/safety.py
async def pre_tool_check(tool_call: ToolCall) -> None:
    """Validate a tool call before execution.
    
    Raises ToolExecutionError for dangerous operations.
    """
    if tool_call.name == "bash":
        check_bash_command_safety(tool_call.arguments.get("command", ""))
    
    if tool_call.name in ("read", "write", "edit", "glob"):
        check_path_safety(tool_call.arguments.get("path", ""))
```

### Streaming

The loop supports streaming responses:

```python
async for event in provider.chat(messages, tools=tools, stream=True):
    if event.type == "content_block_delta":
        yield event.content  # Token by token
    elif event.type == "tool_call":
        tool_calls.append(event.tool_call)
```

### Conversation History

```python
# From karna/models.py
class Conversation(BaseModel):
    messages: list[Message] = []
    
class Message(BaseModel):
    role: str  # system, user, assistant, tool
    content: str
    tool_calls: list[ToolCall] = []
    tool_results: list[ToolResult] = []
```

---

## Session Management

Sessions are stored locally in SQLite and never transmitted externally.

### Session Database

```python
# From karna/sessions/db.py
class SessionDB:
    """SQLite-backed session storage at ~/.karna/sessions/sessions.db"""
    
    def save_message(self, session_id: str, message: Message) -> None: ...
    def get_conversation(self, session_id: str) -> Conversation: ...
    def list_sessions(self) -> list[SessionSummary]: ...
    def delete_session(self, session_id: str) -> None: ...
```

### Cost Tracking

```python
# From karna/sessions/cost.py
class CostTracker:
    """Track usage and cost per session and overall."""
    
    def record_usage(self, session_id: str, usage: Usage) -> None: ...
    def get_session_cost(self, session_id: str) -> Decimal: ...
    def get_total_cost(self) -> Decimal: ...
```

### Privacy Model

- All data stored at `~/.karna/sessions/`
- Session files are `0600` permission
- No session data sent to any external service
- Sessions can be deleted individually or wholesale

---

## Security Model

### Path Traversal Prevention

```python
# From karna/security/guards.py
_SENSITIVE_PATHS = [
    "/etc/shadow",
    "/etc/passwd",
    "/etc/sudoers",
]

_SENSITIVE_PREFIXES = [
    "~/.ssh",
    "~/.karna/credentials",
    "/dev/",
    "/proc/",
    "/sys/",
]

def is_safe_path(path: str, allowed_roots: list[Path] | None = None) -> bool:
    """Reject paths that escape working directory or hit sensitive locations."""
```

### Secret Detection and Scrubbing

```python
# From karna/security/scrub.py
SECRET_PATTERNS = [
    (r'api[_-]?key["\']?\s*[:=]\s*["\']?[\w-]{20,}', '<API_KEY>'),
    (r'password["\']?\s*[:=]\s*["\']?[^\s"\']+', '<PASSWORD>'),
    # ... more patterns
]

def scrub_secrets(text: str) -> str:
    """Replace detected secrets with placeholders."""
```

### SSRF Protection for Web Fetch

```python
def is_safe_url(url: str) -> bool:
    """Block internal IPs and localhost for web fetch."""
    # Rejects: 127.0.0.1, localhost, 0.0.0.0, 169.254.169.254
    # (metadata endpoint for cloud instances)
```

### Dangerous Command Detection

Bash commands can be blocked in `safe_mode`:

