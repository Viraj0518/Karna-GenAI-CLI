"""Smoke tests for the REST server (karna.rest_server).

Exercises the HTTP surface without invoking the agent loop — tests
session CRUD, tool listing, health, 404 paths. Agent-turn tests need
a live provider and belong in an integration suite.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from karna.rest_server import create_app  # noqa: E402


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["server"] == "nellie-rest"


def test_tools_list(client):
    r = client.get("/v1/tools")
    assert r.status_code == 200
    tools = r.json()["tools"]
    names = {t["name"] for t in tools}
    # 19 registered tools per karna/tools/__init__.py
    assert "bash" in names
    assert "read" in names
    assert "write" in names
    assert "mcp" in names
    assert len(names) >= 18


def test_session_create_and_get(client):
    r = client.post(
        "/v1/sessions",
        json={"workspace": "/tmp/nellie-rest-test", "model": "openrouter:anthropic/claude-haiku-4.5"},
    )
    assert r.status_code == 200
    sid = r.json()["id"]
    assert sid

    r2 = client.get(f"/v1/sessions/{sid}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["id"] == sid
    assert body["workspace"] == "/tmp/nellie-rest-test"
    assert body["model"] == "openrouter:anthropic/claude-haiku-4.5"
    assert body["messages"] == []


def test_session_list_reflects_created(client):
    before = len(client.get("/v1/sessions").json()["sessions"])
    client.post("/v1/sessions", json={})
    client.post("/v1/sessions", json={})
    after = len(client.get("/v1/sessions").json()["sessions"])
    assert after == before + 2


def test_session_close(client):
    sid = client.post("/v1/sessions", json={}).json()["id"]
    r = client.delete(f"/v1/sessions/{sid}")
    assert r.status_code == 200
    r2 = client.get(f"/v1/sessions/{sid}")
    assert r2.status_code == 404


def test_session_get_404(client):
    r = client.get("/v1/sessions/does-not-exist")
    assert r.status_code == 404


def test_session_delete_404(client):
    r = client.delete("/v1/sessions/does-not-exist")
    assert r.status_code == 404


def test_session_message_404_on_missing_session(client):
    r = client.post("/v1/sessions/does-not-exist/messages", json={"content": "hi"})
    assert r.status_code == 404


def test_session_message_400_on_empty_content(client):
    sid = client.post("/v1/sessions", json={}).json()["id"]
    r = client.post(f"/v1/sessions/{sid}/messages", json={"content": ""})
    assert r.status_code == 400


def test_session_message_400_on_non_string_content(client):
    sid = client.post("/v1/sessions", json={}).json()["id"]
    r = client.post(f"/v1/sessions/{sid}/messages", json={"content": 42})
    assert r.status_code == 400


def test_session_create_with_system_instructions(client):
    r = client.post(
        "/v1/sessions",
        json={"system_instructions": "You are a Karna consultant assistant."},
    )
    sid = r.json()["id"]
    body = client.get(f"/v1/sessions/{sid}").json()
    assert body["messages"][0]["role"] == "system"
    assert "Karna" in body["messages"][0]["content"]


def test_openapi_spec_generates(client):
    """The OpenAPI spec is how other clients (like Goose's recipe UI)
    discover the surface. Must parse cleanly."""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert "paths" in spec
    assert "/v1/sessions" in spec["paths"]
    assert "/v1/sessions/{sid}/messages" in spec["paths"]
