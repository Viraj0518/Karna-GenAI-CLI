"""Tests for the Memory MCP server (karna.mcp_server.memory_server).

Protocol-level tests (initialize, tools/list, malformed JSON, unknown
method) spawn the server as a subprocess and talk JSON-RPC over pipes.

Tool-level tests (save, get, list, delete) call the handler functions
in-process with a patched MemoryManager pointing at a tmp_path, which
avoids touching the real ``~/.karna/memory`` directory.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any
from unittest.mock import patch

import pytest

from karna.mcp_server.memory_server import (
    _handle_request,
)
from karna.memory.manager import MemoryManager

# ----------------------------------------------------------------------- #
#  Subprocess helpers (protocol-level tests)
# ----------------------------------------------------------------------- #

_SERVER_CMD = [sys.executable, "-m", "karna.mcp_server.memory_server"]


def _send_recv(
    proc: subprocess.Popen,
    method: str,
    params: dict[str, Any] | None = None,
    req_id: int = 1,
) -> dict[str, Any]:
    """Send a JSON-RPC request to a subprocess and read the response."""
    msg: dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": method,
        "id": req_id,
    }
    if params is not None:
        msg["params"] = params

    assert proc.stdin is not None
    assert proc.stdout is not None

    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    assert line, f"Server returned no response for {method}"
    return json.loads(line)


@pytest.fixture()
def server():
    """Start the memory MCP server as a subprocess, yield it, then shut down."""
    proc = subprocess.Popen(
        _SERVER_CMD,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    yield proc
    try:
        assert proc.stdin is not None
        shutdown_msg = json.dumps({"jsonrpc": "2.0", "method": "shutdown", "id": 999}) + "\n"
        proc.stdin.write(shutdown_msg)
        proc.stdin.flush()
    except (BrokenPipeError, OSError):
        pass
    proc.wait(timeout=5)


@pytest.fixture()
def initialized_server(server):
    """Subprocess server that has completed the initialize handshake."""
    resp = _send_recv(
        server,
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.0.1"},
        },
        req_id=1,
    )
    assert "result" in resp
    # Send initialized notification (no response expected)
    assert server.stdin is not None
    server.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    server.stdin.flush()
    return server


# ----------------------------------------------------------------------- #
#  In-process helpers (tool-level tests)
# ----------------------------------------------------------------------- #


def _rpc(method: str, params: dict[str, Any] | None = None, req_id: int = 1) -> dict[str, Any]:
    """Call _handle_request directly and return the response dict."""
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": req_id}
    if params is not None:
        msg["params"] = params
    resp = _handle_request(msg)
    assert resp is not None
    return resp


def _call_tool(tool_name: str, arguments: dict[str, Any], req_id: int = 1) -> dict[str, Any]:
    """Shorthand for tools/call via _handle_request."""
    return _rpc("tools/call", {"name": tool_name, "arguments": arguments}, req_id)


@pytest.fixture()
def mem_mgr(tmp_path):
    """Patch _get_memory_manager to use a MemoryManager in tmp_path."""
    mgr = MemoryManager(memory_dir=tmp_path)
    with patch("karna.mcp_server.memory_server._get_memory_manager", return_value=mgr):
        yield mgr


# ======================================================================= #
#  Protocol tests (subprocess)
# ======================================================================= #


class TestInitialize:
    def test_initialize_handshake(self, server):
        """Server responds to initialize with serverInfo and protocolVersion."""
        resp = _send_recv(
            server,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.0.1"},
            },
        )
        assert "result" in resp
        result = resp["result"]
        assert result["serverInfo"]["name"] == "nellie-memory"
        assert result["serverInfo"]["version"] == "0.1.0"
        assert result["protocolVersion"] == "2024-11-05"


class TestToolsList:
    def test_tools_list_returns_four_tools(self, initialized_server):
        """tools/list should return exactly 4 memory tools."""
        resp = _send_recv(initialized_server, "tools/list", {}, req_id=2)
        assert "result" in resp
        tools = resp["result"]["tools"]
        assert len(tools) == 4
        names = {t["name"] for t in tools}
        assert names == {"memory_list", "memory_get", "memory_save", "memory_delete"}

    def test_tool_schemas_have_input_schema(self, initialized_server):
        """Each tool should have an inputSchema with type=object."""
        resp = _send_recv(initialized_server, "tools/list", {}, req_id=2)
        for tool in resp["result"]["tools"]:
            schema = tool["inputSchema"]
            assert schema["type"] == "object"
            assert "properties" in schema


class TestProtocolErrors:
    def test_malformed_json_returns_parse_error(self, server):
        """Sending invalid JSON produces a -32700 parse error."""
        assert server.stdin is not None
        assert server.stdout is not None
        server.stdin.write("this is not json\n")
        server.stdin.flush()
        line = server.stdout.readline()
        resp = json.loads(line)
        assert "error" in resp
        assert resp["error"]["code"] == -32700

    def test_invalid_method_returns_error(self, initialized_server):
        """An unknown JSON-RPC method returns -32601."""
        resp = _send_recv(initialized_server, "bogus/method", {}, req_id=70)
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    def test_unknown_tool_returns_error(self, initialized_server):
        """tools/call with an unknown tool name returns -32602."""
        resp = _send_recv(
            initialized_server,
            "tools/call",
            {"name": "nonexistent_tool", "arguments": {}},
            req_id=80,
        )
        assert "error" in resp
        assert resp["error"]["code"] == -32602


# ======================================================================= #
#  Tool-level tests (in-process, patched MemoryManager)
# ======================================================================= #


class TestMemorySaveGet:
    def test_save_and_get_roundtrip(self, mem_mgr):
        """Save a memory via MCP and retrieve it back."""
        # Save
        resp = _call_tool(
            "memory_save",
            {
                "name": "Test Note",
                "type": "reference",
                "description": "A test memory for MCP",
                "body": "This is the body of the test memory.",
            },
        )
        result = resp["result"]
        assert result["isError"] is False
        payload = json.loads(result["content"][0]["text"])
        assert payload["saved"] is True

        # Get
        resp = _call_tool("memory_get", {"name": "Test Note"}, req_id=2)
        result = resp["result"]
        assert result["isError"] is False
        mem = json.loads(result["content"][0]["text"])
        assert mem["name"] == "Test Note"
        assert mem["type"] == "reference"
        assert "body of the test memory" in mem["content"]

    def test_get_is_case_insensitive(self, mem_mgr):
        """memory_get should match names case-insensitively."""
        _call_tool(
            "memory_save",
            {
                "name": "My Memo",
                "type": "user",
                "description": "Test",
                "body": "Body",
            },
        )
        resp = _call_tool("memory_get", {"name": "my memo"}, req_id=2)
        assert resp["result"]["isError"] is False


class TestMemoryList:
    def test_list_all(self, mem_mgr):
        """memory_list returns all saved memories."""
        for i in range(3):
            _call_tool(
                "memory_save",
                {
                    "name": f"Note {i}",
                    "type": "project",
                    "description": f"Description {i}",
                    "body": f"Body {i}",
                },
                req_id=i + 10,
            )

        resp = _call_tool("memory_list", {}, req_id=20)
        items = json.loads(resp["result"]["content"][0]["text"])
        assert len(items) == 3

    def test_list_filter_by_type(self, mem_mgr):
        """memory_list with type filter returns only matching entries."""
        for name, mtype in [("A", "user"), ("B", "feedback"), ("C", "user")]:
            _call_tool(
                "memory_save",
                {
                    "name": name,
                    "type": mtype,
                    "description": f"Desc {name}",
                    "body": f"Body {name}",
                },
            )

        resp = _call_tool("memory_list", {"type": "user"}, req_id=50)
        items = json.loads(resp["result"]["content"][0]["text"])
        assert len(items) == 2
        assert all(i["type"] == "user" for i in items)

    def test_list_empty(self, mem_mgr):
        """memory_list on empty memory returns an empty array."""
        resp = _call_tool("memory_list", {})
        items = json.loads(resp["result"]["content"][0]["text"])
        assert items == []


class TestMemoryDelete:
    def test_save_and_delete(self, mem_mgr):
        """Delete removes the memory; subsequent get fails."""
        _call_tool(
            "memory_save",
            {
                "name": "Doomed Note",
                "type": "feedback",
                "description": "Will be deleted",
                "body": "Temporary body",
            },
        )

        # Delete
        resp = _call_tool("memory_delete", {"name": "Doomed Note"}, req_id=2)
        assert resp["result"]["isError"] is False
        payload = json.loads(resp["result"]["content"][0]["text"])
        assert payload["deleted"] is True

        # Verify gone
        resp = _call_tool("memory_get", {"name": "Doomed Note"}, req_id=3)
        assert resp["result"]["isError"] is True

    def test_delete_nonexistent(self, mem_mgr):
        """Deleting a memory that doesn't exist returns isError."""
        resp = _call_tool("memory_delete", {"name": "ghost"})
        assert resp["result"]["isError"] is True
        assert "not found" in resp["result"]["content"][0]["text"].lower()

    def test_save_list_delete_lifecycle(self, mem_mgr):
        """Full lifecycle: save 2, list (2), delete 1, list (1)."""
        for i in range(2):
            _call_tool(
                "memory_save",
                {
                    "name": f"Note {i}",
                    "type": "project",
                    "description": f"Description {i}",
                    "body": f"Body {i}",
                },
                req_id=20 + i,
            )

        resp = _call_tool("memory_list", {}, req_id=30)
        items = json.loads(resp["result"]["content"][0]["text"])
        assert len(items) == 2

        _call_tool("memory_delete", {"name": "Note 0"}, req_id=31)

        resp = _call_tool("memory_list", {}, req_id=32)
        items = json.loads(resp["result"]["content"][0]["text"])
        assert len(items) == 1
        assert items[0]["name"] == "Note 1"


class TestErrorCases:
    def test_get_nonexistent(self, mem_mgr):
        """memory_get for a name that doesn't exist returns isError."""
        resp = _call_tool("memory_get", {"name": "does-not-exist"})
        assert resp["result"]["isError"] is True
        assert "not found" in resp["result"]["content"][0]["text"].lower()

    def test_save_invalid_type(self, mem_mgr):
        """Saving with an invalid memory type returns isError."""
        resp = _call_tool(
            "memory_save",
            {
                "name": "Bad Type",
                "type": "invalid_type",
                "description": "This should fail",
                "body": "body",
            },
        )
        assert resp["result"]["isError"] is True

    def test_save_missing_fields(self, mem_mgr):
        """memory_save with missing required fields returns isError."""
        resp = _call_tool("memory_save", {"name": "Incomplete"})
        assert resp["result"]["isError"] is True
        assert "missing" in resp["result"]["content"][0]["text"].lower()

    def test_get_missing_name(self, mem_mgr):
        """memory_get without a name returns isError."""
        resp = _call_tool("memory_get", {})
        assert resp["result"]["isError"] is True

    def test_delete_missing_name(self, mem_mgr):
        """memory_delete without a name returns isError."""
        resp = _call_tool("memory_delete", {})
        assert resp["result"]["isError"] is True
