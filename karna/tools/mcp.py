"""MCP client tool — connects to MCP servers and proxies their tools.

Implements JSON-RPC 2.0 over stdio to communicate with any standard
MCP (Model Context Protocol) server.  Karna can dynamically discover
and invoke tools exposed by external MCP servers configured in
``~/.karna/config.toml``.

All connections are local-only: the tool starts server processes on the
user's machine and communicates via stdin/stdout.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

import tomli_w

from karna.config import CONFIG_PATH, KARNA_DIR
from karna.tools.base import BaseTool

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30  # seconds per JSON-RPC call


class MCPClientError(RuntimeError):
    """Raised for MCP client-side failures (dead reader, crashed server)."""

# ----------------------------------------------------------------------- #
#  MCP Server Connection
# ----------------------------------------------------------------------- #


class MCPServerConnection:
    """Manages a single MCP server subprocess and the JSON-RPC protocol."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.process: asyncio.subprocess.Process | None = None
        self.tools: list[dict[str, Any]] = []
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        # CRITICAL fix: when the reader loop crashes (malformed JSON,
        # dead subprocess) every subsequent call() would hang for
        # _REQUEST_TIMEOUT seconds before failing.  Track liveness
        # explicitly so we fail fast.
        self._dead: bool = False
        self._dead_reason: str = ""

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """Start the server subprocess and perform the MCP handshake."""
        # Build environment: inherit current env + overlay configured vars
        proc_env = dict(os.environ)
        proc_env.update(self.env)

        cmd_parts = [self.command] + self.args
        self.process = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=proc_env,
        )

        # Start background reader for stdout
        self._reader_task = asyncio.create_task(self._read_loop())

        # MCP initialize handshake
        init_result = await self.call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "karna", "version": "0.1.3"},
            },
        )
        logger.info("MCP server %s initialized: %s", self.name, init_result.get("serverInfo", {}))

        # Send initialized notification (no response expected)
        await self._send_notification("notifications/initialized", {})

        # Fetch available tools
        tools_result = await self.call("tools/list", {})
        self.tools = tools_result.get("tools", [])
        logger.info("MCP server %s exposes %d tools", self.name, len(self.tools))

    async def stop(self) -> None:
        """Graceful shutdown of the server process."""
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        if self.process and self.process.returncode is None:
            try:
                self.process.stdin.close()  # type: ignore[union-attr]
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                self.process.kill()
                await self.process.wait()

        self.process = None
        self.tools = []
        self._pending.clear()

    # ------------------------------------------------------------------ #
    #  JSON-RPC protocol
    # ------------------------------------------------------------------ #

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON-RPC request and wait for the response."""
        if self._dead:
            raise MCPClientError(f"MCP server {self.name} is dead: {self._dead_reason}")
        self._request_id += 1
        req_id = self._request_id

        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": req_id,
        }

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = future

        await self._send(msg)

        try:
            result = await asyncio.wait_for(future, timeout=_REQUEST_TIMEOUT)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"MCP call {method} to {self.name} timed out after {_REQUEST_TIMEOUT}s")

        if "error" in result:
            err = result["error"]
            raise RuntimeError(f"MCP error from {self.name}: {err.get('message', err)}")

        return result.get("result", {})

    async def _send(self, msg: dict[str, Any]) -> None:
        """Write a JSON-RPC message to the server's stdin."""
        if not self.process or not self.process.stdin:
            raise RuntimeError(f"MCP server {self.name} is not running")

        line = json.dumps(msg) + "\n"
        self.process.stdin.write(line.encode())
        await self.process.stdin.drain()

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        await self._send(msg)

    async def _read_loop(self) -> None:
        """Background task: read JSON-RPC messages from stdout and dispatch.

        On ANY exception (including EOF/dead process), mark the
        connection dead and fail all pending futures fast.  Without
        this, every pending ``call()`` would hang for the full
        ``_REQUEST_TIMEOUT`` window (30 s) before raising.
        """
        if not self.process or not self.process.stdout:
            self._mark_dead("reader started with no stdout")
            return

        exc_reason: str | None = None
        try:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    exc_reason = "EOF on stdout (server exited)"
                    break

                line_str = line.decode().strip()
                if not line_str:
                    continue

                try:
                    msg = json.loads(line_str)
                except json.JSONDecodeError:
                    logger.warning("MCP %s: non-JSON line: %s", self.name, line_str[:200])
                    continue

                # Match response to pending request
                msg_id = msg.get("id")
                if msg_id is not None and msg_id in self._pending:
                    future = self._pending.pop(msg_id)
                    if not future.done():
                        future.set_result(msg)
                elif msg.get("method"):
                    # Server-initiated notification — log and skip
                    logger.debug("MCP %s notification: %s", self.name, msg.get("method"))
                else:
                    logger.debug("MCP %s: unmatched message id=%s", self.name, msg_id)

        except asyncio.CancelledError:
            # Clean shutdown — don't mark dead or fail pending futures
            # (stop() is coordinating the teardown).
            return
        except Exception as exc:
            exc_reason = f"reader crashed: {exc}"
            logger.error("MCP %s reader crashed: %s", self.name, exc)

        # Reader loop has ended (EOF or exception).  Mark dead and
        # fail every pending future so callers get a fast error
        # instead of timing out one by one.
        self._mark_dead(exc_reason or "reader loop ended")

    def _mark_dead(self, reason: str) -> None:
        """Mark the connection dead, fail pending futures, close pipes."""
        if self._dead:
            return
        self._dead = True
        self._dead_reason = reason

        pending = self._pending
        self._pending = {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(MCPClientError(reason))

        # Best-effort pipe cleanup.  Swallow errors: if the process
        # is already gone these will raise ProcessLookupError etc.
        proc = self.process
        if proc is not None:
            try:
                if proc.stdin and not proc.stdin.is_closing():
                    proc.stdin.close()
            except Exception:
                pass


# ----------------------------------------------------------------------- #
#  MCP Client Tool
# ----------------------------------------------------------------------- #


class MCPClientTool(BaseTool):
    """Meta-tool that connects to MCP servers and proxies their tools.

    Reads server configurations from ``~/.karna/config.toml`` under
    ``[mcp.servers.<name>]`` sections.

    This tool is not directly called by the model — instead, the tools
    it discovers are individually registered in the agent loop's tool
    list via ``get_all_mcp_tools()``.
    """

    name = "mcp"
    description = "Connect to and use tools from MCP (Model Context Protocol) servers."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "Name of the MCP server to interact with",
            },
            "action": {
                "type": "string",
                "enum": ["connect", "list_tools", "call_tool"],
                "description": "Action to perform",
            },
            "tool_name": {
                "type": "string",
                "description": "Tool name for call_tool action",
            },
            "arguments": {
                "type": "object",
                "description": "Arguments for call_tool action",
            },
        },
        "required": ["server", "action"],
    }

    def __init__(self) -> None:
        self.servers: dict[str, MCPServerConnection] = {}
        self._server_configs: dict[str, dict[str, Any]] = {}
        self._load_configured_servers()

    def _load_configured_servers(self) -> None:
        """Load MCP server configs from ~/.karna/config.toml.

        A malformed TOML file is surfaced (logged + printed) rather than
        silently producing "no servers configured", which previously
        made broken configs look identical to no config at all.
        """
        if not CONFIG_PATH.exists():
            return

        try:
            raw = CONFIG_PATH.read_bytes()
            data = tomllib.loads(raw.decode())
        except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
            logger.error("MCP config parse failed for %s: %s", CONFIG_PATH, exc)
            print(
                f"[mcp] WARN: failed to load {CONFIG_PATH}: {exc}",
                file=sys.stderr,
            )
            return

        mcp_section = data.get("mcp", {})
        servers = mcp_section.get("servers", {})

        for name, cfg in servers.items():
            self._server_configs[name] = cfg

    async def execute(self, **kwargs: Any) -> str:
        """Dispatch to connect / list_tools / call_tool."""
        server_name = kwargs.get("server", "")
        action = kwargs.get("action", "")

        if action == "connect":
            return await self._connect(server_name)
        elif action == "list_tools":
            return self._list_tools(server_name)
        elif action == "call_tool":
            tool_name = kwargs.get("tool_name", "")
            arguments = kwargs.get("arguments", {})
            return await self.call_tool(server_name, tool_name, arguments)
        else:
            return f"[error] Unknown action: {action}"

    async def _connect(self, server_name: str) -> str:
        """Connect to a configured MCP server."""
        if server_name in self.servers:
            return f"Already connected to {server_name} ({len(self.servers[server_name].tools)} tools)"

        cfg = self._server_configs.get(server_name)
        if cfg is None:
            return (
                f"[error] No MCP server configured with name '{server_name}'. "
                f"Available: {', '.join(self._server_configs) or '(none)'}"
            )

        conn = MCPServerConnection(
            name=server_name,
            command=cfg.get("command", ""),
            args=cfg.get("args", []),
            env=cfg.get("env", {}),
        )

        try:
            await conn.start()
        except Exception as exc:
            return f"[error] Failed to start MCP server '{server_name}': {exc}"

        self.servers[server_name] = conn
        tool_names = [t.get("name", "?") for t in conn.tools]
        return f"Connected to {server_name}. Tools: {', '.join(tool_names)}"

    def _list_tools(self, server_name: str) -> str:
        """List tools available on a connected server."""
        conn = self.servers.get(server_name)
        if conn is None:
            return f"[error] Not connected to '{server_name}'. Use action=connect first."

        if not conn.tools:
            return f"Server {server_name} exposes no tools."

        lines = [f"Tools from {server_name}:"]
        for tool in conn.tools:
            name = tool.get("name", "?")
            desc = tool.get("description", "")
            lines.append(f"  - {name}: {desc}")
        return "\n".join(lines)

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        """Call a tool on a connected MCP server via JSON-RPC."""
        conn = self.servers.get(server_name)
        if conn is None:
            return f"[error] Not connected to '{server_name}'"

        try:
            result = await conn.call(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
            )
        except Exception as exc:
            return f"[error] MCP tool call failed: {exc}"

        # MCP tool results contain a "content" array of content blocks
        content_blocks = result.get("content", [])
        parts: list[str] = []
        for block in content_blocks:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                else:
                    parts.append(json.dumps(block))
            else:
                parts.append(str(block))

        return "\n".join(parts) if parts else json.dumps(result)

    async def connect_all(self) -> list[str]:
        """Connect to all configured servers. Returns list of status messages."""
        results = []
        for name in self._server_configs:
            msg = await self._connect(name)
            results.append(msg)
        return results

    def get_all_mcp_tools(self) -> list[dict[str, Any]]:
        """Return all tools from all connected servers in OpenAI tool format.

        Each tool is prefixed with the server name to avoid collisions:
        ``mcp__<server>__<tool_name>``.
        """
        all_tools: list[dict[str, Any]] = []
        for server_name, conn in self.servers.items():
            for tool in conn.tools:
                prefixed_name = f"mcp__{server_name}__{tool.get('name', 'unknown')}"
                schema = tool.get("inputSchema", tool.get("input_schema", {"type": "object", "properties": {}}))
                all_tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": prefixed_name,
                            "description": tool.get("description", ""),
                            "parameters": schema,
                        },
                    }
                )
        return all_tools

    def get_mcp_proxy_tools(self) -> list["MCPProxyTool"]:
        """Return a list of ``MCPProxyTool`` instances for all connected servers.

        These can be added directly to the agent loop's tool list.
        """
        proxies: list[MCPProxyTool] = []
        for server_name, conn in self.servers.items():
            for tool in conn.tools:
                proxies.append(MCPProxyTool(self, server_name, tool))
        return proxies

    async def shutdown(self) -> None:
        """Disconnect from all servers."""
        for conn in self.servers.values():
            await conn.stop()
        self.servers.clear()


class MCPProxyTool(BaseTool):
    """A thin wrapper that makes a single MCP tool look like a ``BaseTool``.

    Created dynamically by ``MCPClientTool.get_mcp_proxy_tools()``
    so the agent loop can use MCP tools like any other built-in tool.
    """

    def __init__(
        self,
        client: MCPClientTool,
        server_name: str,
        tool_spec: dict[str, Any],
    ) -> None:
        self._client = client
        self._server_name = server_name
        self.name = f"mcp__{server_name}__{tool_spec.get('name', 'unknown')}"
        self.description = tool_spec.get("description", "")
        self.parameters = tool_spec.get(
            "inputSchema",
            tool_spec.get("input_schema", {"type": "object", "properties": {}}),
        )
        self._remote_tool_name = tool_spec.get("name", "")

    async def execute(self, **kwargs: Any) -> str:
        """Proxy the call to the MCP server."""
        return await self._client.call_tool(
            self._server_name,
            self._remote_tool_name,
            kwargs,
        )


# ----------------------------------------------------------------------- #
#  Config helpers (for CLI)
# ----------------------------------------------------------------------- #


def _load_raw_config() -> dict[str, Any]:
    """Load the raw TOML config as a dict."""
    if CONFIG_PATH.exists():
        return tomllib.loads(CONFIG_PATH.read_bytes().decode())
    return {}


def _save_raw_config(data: dict[str, Any]) -> None:
    """Save a raw dict back to config.toml."""
    KARNA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_bytes(tomli_w.dumps(data).encode())


def add_mcp_server(
    name: str,
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> None:
    """Add or update an MCP server in the config file."""
    data = _load_raw_config()
    if "mcp" not in data:
        data["mcp"] = {}
    if "servers" not in data["mcp"]:
        data["mcp"]["servers"] = {}

    server_cfg: dict[str, Any] = {"command": command}
    if args:
        server_cfg["args"] = args
    if env:
        server_cfg["env"] = env

    data["mcp"]["servers"][name] = server_cfg
    _save_raw_config(data)


def remove_mcp_server(name: str) -> bool:
    """Remove an MCP server from the config. Returns True if found."""
    data = _load_raw_config()
    servers = data.get("mcp", {}).get("servers", {})
    if name not in servers:
        return False
    del servers[name]
    _save_raw_config(data)
    return True


def list_mcp_servers() -> dict[str, dict[str, Any]]:
    """Return all configured MCP servers."""
    data = _load_raw_config()
    return data.get("mcp", {}).get("servers", {})
