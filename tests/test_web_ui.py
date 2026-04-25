"""Tests for the Nellie Web UI (karna.web).

Exercises all page routes and static assets using the FastAPI TestClient.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from karna.web.app import create_web_app  # noqa: E402


@pytest.fixture
def client():
    app = create_web_app()
    with TestClient(app) as c:
        yield c


# ------------------------------------------------------------------ #
#  Index / session list
# ------------------------------------------------------------------ #


def test_index_returns_200(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_index_contains_session_heading(client):
    r = client.get("/")
    assert "Sessions" in r.text


def test_index_contains_new_session_button(client):
    r = client.get("/")
    assert "New Session" in r.text


# ------------------------------------------------------------------ #
#  Session create + detail
# ------------------------------------------------------------------ #


def test_session_create_redirects(client):
    r = client.post("/sessions/new", follow_redirects=False)
    assert r.status_code == 303
    assert "/sessions/" in r.headers["location"]


def test_session_detail_renders(client):
    # Create a session via REST API
    r = client.post("/api/v1/sessions", json={})
    assert r.status_code == 200
    sid = r.json()["id"]

    # Fetch the session detail page
    r2 = client.get(f"/sessions/{sid}")
    assert r2.status_code == 200
    assert sid[:8] in r2.text
    assert "Send" in r2.text


def test_session_detail_404_for_missing(client):
    r = client.get("/sessions/nonexistent-id")
    assert r.status_code == 404


# ------------------------------------------------------------------ #
#  Session actions
# ------------------------------------------------------------------ #


def test_session_delete_redirects(client):
    r = client.post("/api/v1/sessions", json={})
    sid = r.json()["id"]
    r2 = client.post(f"/sessions/{sid}/delete", follow_redirects=False)
    assert r2.status_code == 303
    assert r2.headers["location"] == "/"


def test_session_cancel_returns_html(client):
    r = client.post("/api/v1/sessions", json={})
    sid = r.json()["id"]
    r2 = client.post(f"/sessions/{sid}/cancel")
    assert r2.status_code == 200
    assert "Cancelled" in r2.text


# ------------------------------------------------------------------ #
#  Recipes page
# ------------------------------------------------------------------ #


def test_recipes_returns_200(client):
    r = client.get("/recipes")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_recipes_contains_heading(client):
    r = client.get("/recipes")
    assert "Recipe Library" in r.text


# ------------------------------------------------------------------ #
#  Memory browser
# ------------------------------------------------------------------ #


def test_memory_returns_200(client):
    r = client.get("/memory")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_memory_contains_heading(client):
    r = client.get("/memory")
    assert "Memory Browser" in r.text


def test_memory_filter_param_accepted(client):
    r = client.get("/memory?type_filter=user")
    assert r.status_code == 200


# ------------------------------------------------------------------ #
#  Static CSS
# ------------------------------------------------------------------ #


def test_static_css_loads(client):
    r = client.get("/static/style.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"]
    assert "--karna-blue-start" in r.text


# ------------------------------------------------------------------ #
#  Navigation
# ------------------------------------------------------------------ #


def test_navbar_present_on_all_pages(client):
    for url in ["/", "/recipes", "/memory"]:
        r = client.get(url)
        assert r.status_code == 200
        assert "Nellie" in r.text
        assert "nav-link" in r.text


# ------------------------------------------------------------------ #
#  REST API still accessible under /api
# ------------------------------------------------------------------ #


def test_api_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_api_sessions_accessible(client):
    r = client.get("/api/v1/sessions")
    assert r.status_code == 200
    assert "sessions" in r.json()
