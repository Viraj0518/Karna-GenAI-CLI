"""Tests for the REST server WebSocket endpoint (Goose-parity row #16)."""

from __future__ import annotations

import pytest


@pytest.fixture()
def client():
    pytest.importorskip("fastapi")
    pytest.importorskip("starlette")
    from fastapi.testclient import TestClient

    from karna.rest_server.app import create_app

    app = create_app()
    return TestClient(app)


def test_ws_rejects_unknown_session(client):
    """Connecting to a non-existent session id should close 4404."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/v1/ws/sessions/does-not-exist"):
            pass
    # FastAPI's TestClient may coerce custom close codes; accept either
    # 4404 (our explicit code) or 1008 (policy violation fallback).
    assert exc.value.code in (4404, 1008)


def test_ws_ping_returns_pong(client):
    """The ``ping`` control message should round-trip as ``pong``."""
    resp = client.post("/v1/sessions", json={"workspace": None, "model": None})
    sid = resp.json()["id"]
    with client.websocket_connect(f"/v1/ws/sessions/{sid}") as ws:
        ws.send_json({"type": "ping"})
        data = ws.receive_json()
        assert data == {"kind": "pong"}


def test_ws_rejects_unknown_control_type(client):
    """Unknown control messages should come back as an ``error`` event."""
    resp = client.post("/v1/sessions", json={"workspace": None, "model": None})
    sid = resp.json()["id"]
    with client.websocket_connect(f"/v1/ws/sessions/{sid}") as ws:
        ws.send_json({"type": "teleport", "content": "anywhere"})
        data = ws.receive_json()
        assert data["kind"] == "error"
        assert "teleport" in data["text"]


def test_ws_empty_message_returns_error(client):
    resp = client.post("/v1/sessions", json={"workspace": None, "model": None})
    sid = resp.json()["id"]
    with client.websocket_connect(f"/v1/ws/sessions/{sid}") as ws:
        ws.send_json({"type": "message", "content": "   "})
        data = ws.receive_json()
        assert data["kind"] == "error"
        assert "empty" in data["text"].lower()


def test_ws_cancel_when_no_active_task(client):
    """``cancel`` with no in-flight turn should still send the control ack."""
    resp = client.post("/v1/sessions", json={"workspace": None, "model": None})
    sid = resp.json()["id"]
    with client.websocket_connect(f"/v1/ws/sessions/{sid}") as ws:
        ws.send_json({"type": "cancel"})
        data = ws.receive_json()
        assert data == {"kind": "cancelled"}
