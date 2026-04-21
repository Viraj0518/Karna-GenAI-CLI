# MCP Servers

Nellie exposes two MCP (Model Context Protocol) servers that external tools,
desktop UIs, and other agents can connect to over JSON-RPC 2.0 stdio.

## `nellie mcp serve` -- Agent tool

Spawns a full Nellie agent loop.  The MCP client sends a prompt, and
Nellie drives the same tool-use loop as the interactive REPL, returning
the agent's final text reply.

**Tool exposed:** `nellie_agent(prompt, model?, max_iterations?, workspace?, include_events?)`

### Config for Claude Desktop / MCP clients

```json
{
  "mcpServers": {
    "nellie": {
      "command": "nellie",
      "args": ["mcp", "serve"]
    }
  }
}
```

### Example JSON-RPC call

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "id": 3,
  "params": {
    "name": "nellie_agent",
    "arguments": {
      "prompt": "Summarise the README in this repo",
      "workspace": "/home/user/my-project"
    }
  }
}
```

---

## `nellie mcp serve-memory` -- Memory read/write surface

Exposes Nellie's persistent memory system (`~/.karna/memory/`) so
external tools can list, read, create, and delete memories without
running a full agent loop.

**Tools exposed:**

| Tool | Description |
|------|-------------|
| `memory_list(type?)` | List all memories.  Returns `[{name, type, description, age}]`.  Optional type filter. |
| `memory_get(name)` | Get full content of a memory by name (case-insensitive match). |
| `memory_save(name, type, description, body)` | Save a new memory.  Valid types: `user`, `feedback`, `project`, `reference` (+ custom). |
| `memory_delete(name)` | Delete a memory by name. |

### Config for Claude Desktop / MCP clients

```json
{
  "mcpServers": {
    "nellie-memory": {
      "command": "nellie",
      "args": ["mcp", "serve-memory"]
    }
  }
}
```

### Example JSON-RPC calls

**List all feedback memories:**

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "id": 1,
  "params": {
    "name": "memory_list",
    "arguments": {"type": "feedback"}
  }
}
```

**Save a new memory:**

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "id": 2,
  "params": {
    "name": "memory_save",
    "arguments": {
      "name": "Preferred code style",
      "type": "feedback",
      "description": "User prefers functional style with type hints",
      "body": "Always use type hints on function signatures.  Prefer list comprehensions over map/filter.  Keep functions under 30 lines."
    }
  }
}
```

**Get a memory by name:**

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "id": 3,
  "params": {
    "name": "memory_get",
    "arguments": {"name": "Preferred code style"}
  }
}
```

**Delete a memory:**

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "id": 4,
  "params": {
    "name": "memory_delete",
    "arguments": {"name": "Preferred code style"}
  }
}
```

---

## Protocol details

Both servers speak **JSON-RPC 2.0** line-delimited over **stdin/stdout**.
Logging goes to stderr.

Standard MCP lifecycle:

1. Client sends `initialize` with `protocolVersion`, `capabilities`, `clientInfo`.
2. Server responds with `protocolVersion`, `capabilities`, `serverInfo`.
3. Client sends `notifications/initialized` (no response).
4. Client calls `tools/list` to discover available tools.
5. Client calls `tools/call` to invoke tools.
6. Client sends `shutdown` or closes stdin to end the session.

Error codes follow JSON-RPC 2.0:

| Code | Meaning |
|------|---------|
| -32700 | Parse error (malformed JSON) |
| -32601 | Method not found |
| -32602 | Invalid params (unknown tool name) |
| -32603 | Internal error |

## Running both servers

You can configure both servers in the same MCP client config.  They
run as separate processes and do not interfere with each other:

```json
{
  "mcpServers": {
    "nellie": {
      "command": "nellie",
      "args": ["mcp", "serve"]
    },
    "nellie-memory": {
      "command": "nellie",
      "args": ["mcp", "serve-memory"]
    }
  }
}
```
