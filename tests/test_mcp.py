"""Tests for the MCP client tool.

Tests use a mock subprocess to simulate the MCP JSON-RPC protocol
without requiring an actual MCP server.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from karna.tools.mcp import (
    MCPClientTool,
    MCPProxyTool,
    MCPServerConnection,
    add_mcp_server,
    list_mcp_servers,
    remove_mcp_server,
)

# ======================================================================= #
#  Helpers
# ======================================================================= #


def _make_jsonrpc_response(req_id: int, result: dict) -> bytes:
    """Build a JSON-RPC response line."""
    return (json.dumps({"jsonrpc": "2.0", "result": result, "id": req_id}) + "\n").encode()


def _make_initialize_response(req_id: int = 1) -> bytes:
    return _make_jsonrpc_response(
        req_id,
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "test-server", "version": "1.0"},
        },
    )


def _make_tools_list_response(req_id: int = 2) -> bytes:
    return _make_jsonrpc_response(
        req_id,
        {
            "tools": [
                {
                    "name": "echo",
                    "description": "Echo the input",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                },
                {
                    "name": "add",
                    "description": "Add two numbers",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "a": {"type": "number"},
                            "b": {"type": "number"},
                        },
                        "required": ["a", "b"],
                    },
                },
            ]
        },
    )


def _make_tool_call_response(req_id: int, text: str) -> bytes:
    return _make_jsonrpc_response(
        req_id,
        {"content": [{"type": "text", "text": text}]},
    )


class MockProcess:
    """A mock asyncio subprocess that simulates an MCP server.

    Responses are queued and only released when a request has been
    written to stdin, preventing timing races between the background
    reader task and the ``call()`` method.
    """

    def __init__(self, responses: list[bytes] | None = None):
        self._responses = responses or [
            _make_initialize_response(1),
            _make_tools_list_response(2),
        ]
        self._response_idx = 0
        self._response_ready = asyncio.Event()
        self._write_count = 0
        self.returncode = None
        self.stdin = self._make_stdin()
        self.stdout = self._make_stdout()
        self.stderr = AsyncMock()

    def _make_stdin(self):
        stdin = MagicMock()
        mock_self = self

        def write(data: bytes):
            # Only count JSON-RPC messages with an "id" field (not notifications)
            try:
                msg = json.loads(data.decode())
                if "id" in msg:
                    mock_self._write_count += 1
                    mock_self._response_ready.set()
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        stdin.write = write
        stdin.drain = AsyncMock()
        stdin.close = MagicMock()
        return stdin

    def _make_stdout(self):
        stdout = AsyncMock()
        mock_self = self

        async def readline():
            if mock_self._response_idx < len(mock_self._responses):
                # Wait until a request has been written before releasing response
                await mock_self._response_ready.wait()
                mock_self._response_ready.clear()
                data = mock_self._responses[mock_self._response_idx]
                mock_self._response_idx += 1
                return data
            # No more responses — block forever (simulates server waiting)
            await asyncio.Event().wait()
            return b""  # unreachable, but satisfies type checker

        stdout.readline = readline
        return stdout

    async def wait(self):
        self.returncode = 0
        return 0

    def kill(self):
        self.returncode = -9


# ======================================================================= #
#  MCPServerConnection tests
# ======================================================================= #


class TestMCPServerConnection:
    @pytest.mark.asyncio
    async def test_handshake_and_tool_listing(self):
        """Test that the initialize + tools/list handshake works."""
        mock_proc = MockProcess()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            conn = MCPServerConnection(
                name="test",
                command="echo",
                args=["test"],
            )
            await conn.start()

            # Should have discovered 2 tools
            assert len(conn.tools) == 2
            tool_names = [t["name"] for t in conn.tools]
            assert "echo" in tool_names
            assert "add" in tool_names

            await conn.stop()

    @pytest.mark.asyncio
    async def test_tool_call_roundtrip(self):
        """Test calling a tool and getting a response."""
        responses = [
            _make_initialize_response(1),
            _make_tools_list_response(2),
            _make_tool_call_response(3, "Hello, world!"),
        ]
        mock_proc = MockProcess(responses)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            conn = MCPServerConnection(name="test", command="echo")
            await conn.start()

            result = await conn.call(
                "tools/call",
                {"name": "echo", "arguments": {"text": "Hello, world!"}},
            )

            assert "content" in result
            assert result["content"][0]["text"] == "Hello, world!"

            await conn.stop()

    @pytest.mark.asyncio
    async def test_stop_on_not_started(self):
        """Stop should be safe to call when not started."""
        conn = MCPServerConnection(name="test", command="echo")
        await conn.stop()  # should not raise

    @pytest.mark.asyncio
    async def test_call_error_handling(self):
        """Test that JSON-RPC errors are raised as RuntimeError."""
        error_response = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32601, "message": "Method not found"},
                    "id": 3,
                }
            )
            + "\n"
        ).encode()

        responses = [
            _make_initialize_response(1),
            _make_tools_list_response(2),
            error_response,
        ]
        mock_proc = MockProcess(responses)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            conn = MCPServerConnection(name="test", command="echo")
            await conn.start()

            with pytest.raises(RuntimeError, match="Method not found"):
                await conn.call("nonexistent/method", {})

            await conn.stop()


# ======================================================================= #
#  MCPClientTool tests
# ======================================================================= #


class TestMCPClientTool:
    def test_no_servers_configured(self):
        """Should work with empty config."""
        with patch("karna.tools.mcp.CONFIG_PATH") as mock_path:
            mock_path.exists.return_value = False
            client = MCPClientTool()
            assert client._server_configs == {}

    def test_get_all_mcp_tools_empty(self):
        """No connected servers = no tools."""
        with patch("karna.tools.mcp.CONFIG_PATH") as mock_path:
            mock_path.exists.return_value = False
            client = MCPClientTool()
            assert client.get_all_mcp_tools() == []

    def test_get_all_mcp_tools_format(self):
        """Tool format should match OpenAI function-calling convention."""
        with patch("karna.tools.mcp.CONFIG_PATH") as mock_path:
            mock_path.exists.return_value = False
            client = MCPClientTool()
            # Manually inject a connected server with tools
            conn = MCPServerConnection(name="test", command="echo")
            conn.tools = [
                {
                    "name": "greet",
                    "description": "Say hello",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    },
                }
            ]
            client.servers["test"] = conn

            tools = client.get_all_mcp_tools()
            assert len(tools) == 1
            assert tools[0]["type"] == "function"
            assert tools[0]["function"]["name"] == "mcp__test__greet"
            assert tools[0]["function"]["description"] == "Say hello"

    def test_get_mcp_proxy_tools(self):
        """Proxy tools should be proper BaseTool instances."""
        with patch("karna.tools.mcp.CONFIG_PATH") as mock_path:
            mock_path.exists.return_value = False
            client = MCPClientTool()
            conn = MCPServerConnection(name="srv", command="echo")
            conn.tools = [
                {
                    "name": "ping",
                    "description": "Ping test",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ]
            client.servers["srv"] = conn

            proxies = client.get_mcp_proxy_tools()
            assert len(proxies) == 1
            assert isinstance(proxies[0], MCPProxyTool)
            assert proxies[0].name == "mcp__srv__ping"

            # Should convert to OpenAI format
            oai = proxies[0].to_openai_tool()
            assert oai["type"] == "function"
            assert oai["function"]["name"] == "mcp__srv__ping"

    @pytest.mark.asyncio
    async def test_connect_unknown_server(self):
        """Connecting to an unknown server should return an error."""
        with patch("karna.tools.mcp.CONFIG_PATH") as mock_path:
            mock_path.exists.return_value = False
            client = MCPClientTool()
            result = await client.execute(server="nonexistent", action="connect")
            assert "[error]" in result

    @pytest.mark.asyncio
    async def test_list_tools_not_connected(self):
        """Listing tools without connecting should return an error."""
        with patch("karna.tools.mcp.CONFIG_PATH") as mock_path:
            mock_path.exists.return_value = False
            client = MCPClientTool()
            result = await client.execute(server="nonexistent", action="list_tools")
            assert "[error]" in result


# ======================================================================= #
#  Config helpers tests
# ======================================================================= #


class TestMCPConfigHelpers:
    def test_add_list_remove(self, tmp_path):
        """Test the full lifecycle of adding, listing, and removing servers."""
        config_path = tmp_path / "config.toml"

        with patch("karna.tools.mcp.CONFIG_PATH", config_path), patch("karna.tools.mcp.KARNA_DIR", tmp_path):
            # Add a server
            add_mcp_server("test-srv", "echo", ["hello"], {"FOO": "bar"})

            # List it
            servers = list_mcp_servers()
            assert "test-srv" in servers
            assert servers["test-srv"]["command"] == "echo"
            assert servers["test-srv"]["args"] == ["hello"]
            assert servers["test-srv"]["env"] == {"FOO": "bar"}

            # Remove it
            removed = remove_mcp_server("test-srv")
            assert removed is True

            # Verify gone
            servers = list_mcp_servers()
            assert "test-srv" not in servers

            # Remove nonexistent
            removed = remove_mcp_server("nope")
            assert removed is False
