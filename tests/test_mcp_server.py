"""Tests for the MCP server wrapper (karna/mcp_server).

These exercise the JSON-RPC protocol surface end-to-end without
actually invoking the agent loop (which would need a live provider +
API key). ``initialize``, ``tools/list``, ``ping``, a malformed-tool
call, and an unknown-method call are all covered.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from karna.mcp_server import server as mcp_server


@pytest.mark.asyncio
async def test_initialize_handshake():
    req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    resp = await mcp_server._handle_request(req)
    assert resp is not None
    assert resp["id"] == 1
    result = resp["result"]
    assert "protocolVersion" in result
    assert result["serverInfo"]["name"] == "nellie"
    assert "tools" in result["capabilities"]


@pytest.mark.asyncio
async def test_tools_list_exposes_nellie_agent():
    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    resp = await mcp_server._handle_request(req)
    assert resp is not None
    tools = resp["result"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "nellie_agent"
    schema = tools[0]["inputSchema"]
    assert "prompt" in schema["properties"]
    assert schema["required"] == ["prompt"]


@pytest.mark.asyncio
async def test_ping_returns_empty_result():
    req = {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}}
    resp = await mcp_server._handle_request(req)
    assert resp == {"jsonrpc": "2.0", "id": 3, "result": {}}


@pytest.mark.asyncio
async def test_tools_call_rejects_unknown_tool():
    req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "not_a_tool", "arguments": {}},
    }
    resp = await mcp_server._handle_request(req)
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_tools_call_requires_prompt():
    req = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "nellie_agent", "arguments": {}},
    }
    resp = await mcp_server._handle_request(req)
    assert resp is not None
    assert "error" in resp
    assert "prompt" in resp["error"]["message"].lower()


@pytest.mark.asyncio
async def test_unknown_method_returns_32601():
    req = {"jsonrpc": "2.0", "id": 6, "method": "definitely/not/real", "params": {}}
    resp = await mcp_server._handle_request(req)
    assert resp is not None
    assert resp["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_notification_returns_none():
    # No `id` field — it's a notification, server must not respond.
    req = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    resp = await mcp_server._handle_request(req)
    assert resp is None


@pytest.mark.asyncio
async def test_shutdown_returns_empty_ok():
    req = {"jsonrpc": "2.0", "id": 7, "method": "shutdown", "params": {}}
    resp = await mcp_server._handle_request(req)
    assert resp is not None
    assert resp["result"] == {}


def test_tool_schema_json_serialisable():
    # The schema is returned verbatim to the client; it must JSON-encode
    # cleanly (no tuples, no non-serialisable sentinels).
    json.dumps(mcp_server._NELLIE_AGENT_TOOL)


@pytest.mark.asyncio
async def test_handler_never_hangs_on_empty_params():
    """Params may be missing — handler must use {} as default."""
    req = {"jsonrpc": "2.0", "id": 8, "method": "tools/list"}
    resp = await asyncio.wait_for(
        mcp_server._handle_request(req), timeout=1.0
    )
    assert resp["result"]["tools"][0]["name"] == "nellie_agent"
