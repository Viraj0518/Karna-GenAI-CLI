"""Integration smoke test for the computer_controller MCP (Goose-parity #5).

Now that the server module exists (see commit introducing
``karna/mcp_servers/computer_controller_server.py``), the stub activates
automatically. Exercises:

- MCP ``tools/list`` exposes the expected tool surface
- ``get_screen_size`` returns a well-formed response on hosts with a
  real display; skipped cleanly on headless CI

Thorough protocol coverage + fake-pyautogui happy paths live in
``tests/test_computer_controller_server.py``. This file is the outer
ring — runs against the real display stack when one is available.
"""
from __future__ import annotations

import asyncio
import json

import pytest

pytestmark = pytest.mark.integration


_CC_PATH = "karna.mcp_servers.computer_controller_server"


def _cc_available() -> bool:
    try:
        __import__(_CC_PATH)
        return True
    except ImportError:
        return False


def _has_display() -> bool:
    """Check whether pyautogui's import actually works on this host."""
    if not _cc_available():
        return False
    import importlib
    cc = importlib.import_module(_CC_PATH)
    pg, err = cc._load_pyautogui()  # type: ignore[attr-defined]
    return pg is not None and err is None


@pytest.fixture
def cc_mod():
    if not _cc_available():
        pytest.skip(f"{_CC_PATH} not importable")
    import importlib
    return importlib.import_module(_CC_PATH)


def _call(cc, method: str, params: dict | None = None) -> dict:
    req = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return asyncio.run(cc._handle_request(req))  # type: ignore[attr-defined]


def test_tools_list_exposes_seven_tools(cc_mod):
    """Tool surface is part of the Goose-parity contract — breaking it
    would silently shrink what downstream agents can do."""
    resp = _call(cc_mod, "tools/list")
    assert resp is not None
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {
        "screen_capture",
        "get_screen_size",
        "mouse_move",
        "mouse_click",
        "mouse_scroll",
        "keyboard_type",
        "keyboard_press",
    }


def test_initialize_handshake(cc_mod):
    resp = _call(cc_mod, "initialize")
    result = resp["result"]
    assert result["serverInfo"]["name"] == "nellie-computer-controller"
    assert result["protocolVersion"] == "2024-11-05"


def test_get_screen_size_on_real_display(cc_mod):
    """On a host with a real display, ``get_screen_size`` must return
    positive width + height. On headless hosts, skip cleanly — the
    happy-path behaviour is already covered in the unit tests via a
    fake pyautogui."""
    if not _has_display():
        pytest.skip("no display available — headless CI")
    resp = _call(
        cc_mod,
        "tools/call",
        {"name": "get_screen_size", "arguments": {}},
    )
    result = resp["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["width"] > 0
    assert payload["height"] > 0
