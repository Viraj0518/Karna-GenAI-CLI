"""Tests for the ACP server wrapper (karna/acp_server).

Exercises the JSON-RPC protocol surface end-to-end without invoking the
agent loop — initialize, session/new, session/list, session/close,
session/cancel, plus error paths and notification handling.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from karna.acp_server import server as acp_server


def _write_noop(_msg: dict) -> None:
    """Notification sink — tests that don't drive a prompt discard these."""
    return None


@pytest.fixture(autouse=True)
def _clear_sessions():
    """Reset the module-level session registry between tests."""
    acp_server._sessions.clear()
    yield
    acp_server._sessions.clear()


@pytest.mark.asyncio
async def test_initialize_handshake():
    req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    resp = await acp_server._handle_request(req, _write_noop)
    assert resp is not None
    assert resp["id"] == 1
    result = resp["result"]
    assert "protocolVersion" in result
    assert result["serverInfo"]["name"] == "nellie-acp"
    assert result["capabilities"]["session"]["streaming"] is True


@pytest.mark.asyncio
async def test_session_new_returns_opaque_id():
    req = {"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {}}
    resp = await acp_server._handle_request(req, _write_noop)
    assert resp is not None
    sid = resp["result"]["session_id"]
    assert isinstance(sid, str) and len(sid) >= 8
    assert sid in acp_server._sessions


@pytest.mark.asyncio
async def test_session_new_persists_workspace_and_model():
    req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "session/new",
        "params": {"workspace": "/tmp/acp-ws", "model": "openai:gpt-4o"},
    }
    resp = await acp_server._handle_request(req, _write_noop)
    result = resp["result"]
    assert result["workspace"] == "/tmp/acp-ws"
    assert result["model"] == "openai:gpt-4o"
    session = acp_server._sessions[result["session_id"]]
    assert session.workspace == "/tmp/acp-ws"
    assert session.model == "openai:gpt-4o"


@pytest.mark.asyncio
async def test_session_list_reports_open_sessions():
    # Open two sessions
    for i in (10, 11):
        await acp_server._handle_request(
            {"jsonrpc": "2.0", "id": i, "method": "session/new", "params": {}},
            _write_noop,
        )
    resp = await acp_server._handle_request(
        {"jsonrpc": "2.0", "id": 12, "method": "session/list", "params": {}},
        _write_noop,
    )
    sessions = resp["result"]["sessions"]
    assert len(sessions) == 2
    for s in sessions:
        assert "session_id" in s
        assert s["active"] is False
        assert s["message_count"] == 0


@pytest.mark.asyncio
async def test_session_close_removes_session():
    new_resp = await acp_server._handle_request(
        {"jsonrpc": "2.0", "id": 20, "method": "session/new", "params": {}},
        _write_noop,
    )
    sid = new_resp["result"]["session_id"]
    close_resp = await acp_server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 21,
            "method": "session/close",
            "params": {"session_id": sid},
        },
        _write_noop,
    )
    assert close_resp["result"] == {"closed": sid}
    assert sid not in acp_server._sessions


@pytest.mark.asyncio
async def test_session_close_unknown_id_errors():
    resp = await acp_server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 30,
            "method": "session/close",
            "params": {"session_id": "nonexistent"},
        },
        _write_noop,
    )
    assert "error" in resp
    assert resp["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_session_prompt_rejects_unknown_session():
    resp = await acp_server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 40,
            "method": "session/prompt",
            "params": {"session_id": "nope", "prompt": "hi"},
        },
        _write_noop,
    )
    assert "error" in resp
    assert resp["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_session_prompt_rejects_empty_prompt():
    new_resp = await acp_server._handle_request(
        {"jsonrpc": "2.0", "id": 50, "method": "session/new", "params": {}},
        _write_noop,
    )
    sid = new_resp["result"]["session_id"]
    resp = await acp_server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 51,
            "method": "session/prompt",
            "params": {"session_id": sid, "prompt": "   "},
        },
        _write_noop,
    )
    assert "error" in resp
    assert resp["error"]["code"] == -32602
    assert "prompt" in resp["error"]["message"].lower()


@pytest.mark.asyncio
async def test_session_cancel_with_no_active_task():
    new_resp = await acp_server._handle_request(
        {"jsonrpc": "2.0", "id": 60, "method": "session/new", "params": {}},
        _write_noop,
    )
    sid = new_resp["result"]["session_id"]
    resp = await acp_server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 61,
            "method": "session/cancel",
            "params": {"session_id": sid},
        },
        _write_noop,
    )
    assert resp["result"]["cancelled"] is False


@pytest.mark.asyncio
async def test_ping_returns_empty_result():
    resp = await acp_server._handle_request(
        {"jsonrpc": "2.0", "id": 70, "method": "ping", "params": {}},
        _write_noop,
    )
    assert resp == {"jsonrpc": "2.0", "id": 70, "result": {}}


@pytest.mark.asyncio
async def test_unknown_method_returns_32601():
    resp = await acp_server._handle_request(
        {"jsonrpc": "2.0", "id": 80, "method": "definitely/not/real", "params": {}},
        _write_noop,
    )
    assert resp["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_notification_returns_none():
    # No `id` — notification, no response expected.
    resp = await acp_server._handle_request(
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        _write_noop,
    )
    assert resp is None


@pytest.mark.asyncio
async def test_shutdown_returns_empty_ok():
    resp = await acp_server._handle_request(
        {"jsonrpc": "2.0", "id": 90, "method": "shutdown", "params": {}},
        _write_noop,
    )
    assert resp["result"] == {}


@pytest.mark.asyncio
async def test_handler_defaults_missing_params():
    """Params may be omitted — handler must treat as {}."""
    resp = await asyncio.wait_for(
        acp_server._handle_request(
            {"jsonrpc": "2.0", "id": 100, "method": "session/list"},
            _write_noop,
        ),
        timeout=1.0,
    )
    assert resp["result"]["sessions"] == []


def test_make_notification_shape():
    notif = acp_server._make_notification("session/update", {"session_id": "x", "kind": "text"})
    assert notif["jsonrpc"] == "2.0"
    assert "id" not in notif
    assert notif["method"] == "session/update"
    assert notif["params"]["kind"] == "text"
    # Must JSON-serialise cleanly for stdio transport.
    json.dumps(notif)
