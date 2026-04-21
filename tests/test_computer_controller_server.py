"""Tests for karna.mcp_servers.computer_controller_server (Goose-parity #5).

Strategy:
- Protocol surface (initialize / tools/list / ping / unknown) exercised
  directly through ``_handle_request`` like ``test_mcp_server.py``.
- Tool implementations are tested with a fake pyautogui installed via
  ``monkeypatch`` on ``_load_pyautogui``. This sidesteps the real
  display probe, which can't run on CI Linux, and lets us assert the
  exact pyautogui calls the server issues.
- The headless-fail path is covered once by monkeypatching
  ``_load_pyautogui`` to return an error — asserts that every tool
  returns a structured ``isError`` instead of raising.
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

import pytest

from karna.mcp_servers import computer_controller_server as cc


# ----------------------------------------------------------------------- #
#  Fake pyautogui
# ----------------------------------------------------------------------- #


class _FakePILImage:
    """Smallest PIL-image-like surface the server touches."""

    def __init__(self, width: int = 100, height: int = 50) -> None:
        self.size = (width, height)

    def save(self, buf: io.BytesIO, format: str) -> None:  # noqa: A002
        # Write a trivial PNG-ish payload — content doesn't matter, we
        # only care that the server base64-encodes what we give it.
        buf.write(b"\x89PNG\r\n\x1a\nFAKEIMG")


class _FakePyautogui:
    def __init__(self) -> None:
        self.FAILSAFE = False
        self.calls: list[tuple[str, tuple, dict]] = []
        self._size = (1920, 1080)

    # Log-and-return-fake helpers
    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def screenshot(self, region: tuple[int, int, int, int] | None = None) -> _FakePILImage:
        self._record("screenshot", region=region)
        if region:
            _, _, w, h = region
            return _FakePILImage(w, h)
        return _FakePILImage(1920, 1080)

    def size(self) -> tuple[int, int]:
        self._record("size")
        return self._size

    def moveTo(self, x: int, y: int, duration: float = 0.0) -> None:
        self._record("moveTo", x, y, duration=duration)

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> None:
        self._record("click", x, y, button=button, clicks=clicks)

    def scroll(self, dy: int, x: int | None = None, y: int | None = None) -> None:
        self._record("scroll", dy, x=x, y=y)

    def typewrite(self, text: str, interval: float = 0.0) -> None:
        self._record("typewrite", text, interval=interval)

    def press(self, key: str) -> None:
        self._record("press", key)

    def hotkey(self, *keys: str) -> None:
        self._record("hotkey", *keys)


@pytest.fixture
def fake_pg(monkeypatch: pytest.MonkeyPatch) -> _FakePyautogui:
    pg = _FakePyautogui()
    monkeypatch.setattr(cc, "_load_pyautogui", lambda: (pg, None))
    return pg


@pytest.fixture
def headless(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cc,
        "_load_pyautogui",
        lambda: (None, "pyautogui unavailable: no display"),
    )


# ----------------------------------------------------------------------- #
#  Protocol surface
# ----------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_initialize_handshake():
    req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    resp = await cc._handle_request(req)
    assert resp is not None
    result = resp["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "nellie-computer-controller"
    assert "tools" in result["capabilities"]


@pytest.mark.asyncio
async def test_tools_list_exposes_seven_tools():
    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    resp = await cc._handle_request(req)
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


@pytest.mark.asyncio
async def test_ping_returns_empty_result():
    req = {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}}
    resp = await cc._handle_request(req)
    assert resp == {"jsonrpc": "2.0", "id": 3, "result": {}}


@pytest.mark.asyncio
async def test_unknown_tool_returns_error():
    req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "not_a_tool", "arguments": {}},
    }
    resp = await cc._handle_request(req)
    assert resp is not None
    assert resp["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_unknown_method_returns_method_not_found():
    req = {"jsonrpc": "2.0", "id": 5, "method": "bogus", "params": {}}
    resp = await cc._handle_request(req)
    assert resp is not None
    assert resp["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_shutdown_and_exit_are_accepted():
    for method in ("shutdown", "exit"):
        req = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}
        resp = await cc._handle_request(req)
        assert resp is not None
        assert resp["result"] == {}


@pytest.mark.asyncio
async def test_notifications_do_not_respond():
    req = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    resp = await cc._handle_request(req)
    assert resp is None


# ----------------------------------------------------------------------- #
#  Happy-path tool calls with a faked pyautogui
# ----------------------------------------------------------------------- #


async def _call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    req = {
        "jsonrpc": "2.0",
        "id": 99,
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }
    resp = await cc._handle_request(req)
    assert resp is not None, f"no response for {name}"
    return resp["result"]


@pytest.mark.asyncio
async def test_screen_capture_returns_base64_png(fake_pg: _FakePyautogui):
    result = await _call_tool("screen_capture", {})
    assert result["isError"] is False
    # First block must be a valid base64 image.
    image_block = result["content"][0]
    assert image_block["type"] == "image"
    assert image_block["mimeType"] == "image/png"
    decoded = base64.b64decode(image_block["data"])
    assert decoded.startswith(b"\x89PNG")
    # A size description is included as the second content block.
    assert "1920x1080" in result["content"][1]["text"]
    assert fake_pg.calls[0][0] == "screenshot"


@pytest.mark.asyncio
async def test_screen_capture_region_passes_tuple(fake_pg: _FakePyautogui):
    result = await _call_tool("screen_capture", {"region": [10, 20, 300, 400]})
    assert result["isError"] is False
    (name, args, kwargs) = fake_pg.calls[0]
    assert name == "screenshot"
    assert kwargs["region"] == (10, 20, 300, 400)


@pytest.mark.asyncio
async def test_screen_capture_save_to_writes_file(
    fake_pg: _FakePyautogui, tmp_path
):
    target = tmp_path / "shots" / "out.png"
    result = await _call_tool("screen_capture", {"save_to": str(target)})
    assert result["isError"] is False
    assert target.exists()
    assert target.read_bytes().startswith(b"\x89PNG")
    # save_to path is mentioned in the text block.
    texts = [b["text"] for b in result["content"] if b["type"] == "text"]
    assert any(str(target) in t for t in texts)


@pytest.mark.asyncio
async def test_get_screen_size(fake_pg: _FakePyautogui):
    result = await _call_tool("get_screen_size", {})
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload == {"width": 1920, "height": 1080}


@pytest.mark.asyncio
async def test_mouse_move(fake_pg: _FakePyautogui):
    result = await _call_tool("mouse_move", {"x": 100, "y": 200, "duration": 0.2})
    assert result["isError"] is False
    (name, args, kwargs) = fake_pg.calls[0]
    assert name == "moveTo"
    assert args == (100, 200)
    assert kwargs == {"duration": 0.2}


@pytest.mark.asyncio
async def test_mouse_click_defaults(fake_pg: _FakePyautogui):
    result = await _call_tool("mouse_click", {"x": 5, "y": 6})
    assert result["isError"] is False
    (name, args, kwargs) = fake_pg.calls[0]
    assert name == "click"
    assert args == (5, 6)
    assert kwargs == {"button": "left", "clicks": 1}


@pytest.mark.asyncio
async def test_mouse_click_right_double(fake_pg: _FakePyautogui):
    result = await _call_tool(
        "mouse_click", {"x": 5, "y": 6, "button": "right", "clicks": 2}
    )
    assert result["isError"] is False
    assert fake_pg.calls[0][2] == {"button": "right", "clicks": 2}


@pytest.mark.asyncio
async def test_mouse_click_bad_button(fake_pg: _FakePyautogui):
    result = await _call_tool(
        "mouse_click", {"x": 5, "y": 6, "button": "purple"}
    )
    assert result["isError"] is True
    assert "invalid button" in result["content"][0]["text"]
    # pyautogui was NOT called because we rejected early.
    assert fake_pg.calls == []


@pytest.mark.asyncio
async def test_mouse_click_bad_clicks(fake_pg: _FakePyautogui):
    result = await _call_tool(
        "mouse_click", {"x": 5, "y": 6, "clicks": 99}
    )
    assert result["isError"] is True
    assert "1..5" in result["content"][0]["text"]
    assert fake_pg.calls == []


@pytest.mark.asyncio
async def test_mouse_scroll_without_position(fake_pg: _FakePyautogui):
    result = await _call_tool("mouse_scroll", {"dy": 5})
    assert result["isError"] is False
    (name, args, kwargs) = fake_pg.calls[0]
    assert name == "scroll"
    assert args == (5,)
    assert kwargs == {"x": None, "y": None}


@pytest.mark.asyncio
async def test_mouse_scroll_with_position(fake_pg: _FakePyautogui):
    result = await _call_tool("mouse_scroll", {"dy": -3, "x": 10, "y": 20})
    assert result["isError"] is False
    (name, args, kwargs) = fake_pg.calls[0]
    assert name == "scroll"
    assert args == (-3,)
    assert kwargs == {"x": 10, "y": 20}


@pytest.mark.asyncio
async def test_keyboard_type(fake_pg: _FakePyautogui):
    result = await _call_tool(
        "keyboard_type", {"text": "hello world", "interval": 0.05}
    )
    assert result["isError"] is False
    (name, args, kwargs) = fake_pg.calls[0]
    assert name == "typewrite"
    assert args == ("hello world",)
    assert kwargs == {"interval": 0.05}


@pytest.mark.asyncio
async def test_keyboard_type_rejects_non_string(fake_pg: _FakePyautogui):
    result = await _call_tool("keyboard_type", {"text": 42})
    assert result["isError"] is True
    assert "must be a string" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_keyboard_press_single_key(fake_pg: _FakePyautogui):
    result = await _call_tool("keyboard_press", {"keys": "enter"})
    assert result["isError"] is False
    (name, args, _kwargs) = fake_pg.calls[0]
    assert name == "press"
    assert args == ("enter",)


@pytest.mark.asyncio
async def test_keyboard_press_hotkey_combo(fake_pg: _FakePyautogui):
    result = await _call_tool("keyboard_press", {"keys": "ctrl+shift+t"})
    assert result["isError"] is False
    (name, args, _kwargs) = fake_pg.calls[0]
    assert name == "hotkey"
    assert args == ("ctrl", "shift", "t")


@pytest.mark.asyncio
async def test_keyboard_press_rejects_empty(fake_pg: _FakePyautogui):
    result = await _call_tool("keyboard_press", {"keys": "   "})
    assert result["isError"] is True


# ----------------------------------------------------------------------- #
#  Headless-safe fallback
# ----------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,args",
    [
        ("screen_capture", {}),
        ("get_screen_size", {}),
        ("mouse_move", {"x": 1, "y": 1}),
        ("mouse_click", {"x": 1, "y": 1}),
        ("mouse_scroll", {"dy": 1}),
        ("keyboard_type", {"text": "hi"}),
        ("keyboard_press", {"keys": "enter"}),
    ],
)
async def test_every_tool_fails_gracefully_when_headless(
    headless, tool: str, args: dict[str, Any]
):
    result = await _call_tool(tool, args)
    assert result["isError"] is True
    assert "unavailable" in result["content"][0]["text"].lower()


# ----------------------------------------------------------------------- #
#  Tool handler exceptions are surfaced as isError, not crashes
# ----------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tool_handler_exception_is_surfaced(monkeypatch: pytest.MonkeyPatch):
    """When a tool's handler raises unexpectedly, the server must return
    an ``isError`` result rather than crashing the JSON-RPC loop."""
    async def _boom(args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("synthetic blowup")

    monkeypatch.setitem(cc._DISPATCH, "get_screen_size", _boom)
    result = await _call_tool("get_screen_size", {})
    assert result["isError"] is True
    assert "RuntimeError" in result["content"][0]["text"]
