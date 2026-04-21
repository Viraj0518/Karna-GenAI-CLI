"""JSON-RPC 2.0 over stdio — desktop control as MCP tools.

Goose-parity row #5. Exposes screen capture + keyboard/mouse input to
any MCP client so an agent can drive a real desktop.

Tools:
- ``screen_capture`` — PNG of full screen or a region, returned as a
  base64 ``image`` content block (MCP spec) + optional file path.
- ``get_screen_size`` — ``{width, height}`` of the primary display.
- ``mouse_move`` — move the cursor to ``(x, y)`` with optional duration.
- ``mouse_click`` — click at ``(x, y)`` with button + click-count.
- ``mouse_scroll`` — scroll by ``dy`` vertical clicks (positive = up).
- ``keyboard_type`` — type a string, with optional per-char interval.
- ``keyboard_press`` — press a hotkey combo (e.g. ``"ctrl+c"``).

Headless-safe: pyautogui probes the display on import and raises when
there is none (CI Linux boxes, macOS without accessibility perm, etc.).
We late-import inside each tool call and wrap the probe, returning a
structured ``isError`` result instead of crashing the server.

Protocol matches ``karna.mcp_server.server`` — same ``initialize`` /
``tools/list`` / ``tools/call`` / ``ping`` / ``shutdown`` / ``exit``
surface, same 2024-11-05 protocol version, same stderr-only logging so
the stdout JSON-RPC stream stays clean.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SERVER_NAME = "nellie-computer-controller"
_SERVER_VERSION = "0.1.3"
_PROTOCOL_VERSION = "2024-11-05"


# ----------------------------------------------------------------------- #
#  pyautogui availability — probed lazily
# ----------------------------------------------------------------------- #


def _load_pyautogui() -> tuple[Any | None, str | None]:
    """Return ``(module, None)`` on success, ``(None, error_text)`` on failure.

    pyautogui resolves the display at import time — ``import pyautogui``
    on a headless Linux box raises ``KeyError: 'DISPLAY'`` or
    ``Xlib.error.DisplayConnectionError``. We trap any failure and return
    a structured error so callers see ``"no display available"`` instead
    of a traceback that kills the server.
    """
    try:
        import pyautogui  # type: ignore[import-untyped]

        pyautogui.FAILSAFE = False
        return pyautogui, None
    except Exception as exc:  # noqa: BLE001
        return None, (
            f"pyautogui unavailable: {type(exc).__name__}: {exc}. "
            f"This environment has no accessible display — "
            f"computer_controller tools require a graphical session."
        )


# ----------------------------------------------------------------------- #
#  Tool schemas
# ----------------------------------------------------------------------- #


_TOOLS: list[dict[str, Any]] = [
    {
        "name": "screen_capture",
        "description": (
            "Capture the screen (or a region) and return it as a PNG. "
            "Returns an MCP image content block (base64-encoded PNG). "
            "Optionally writes a copy to disk and returns the path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 4,
                    "maxItems": 4,
                    "description": (
                        "Optional ``[left, top, width, height]``. If omitted, captures the full primary display."
                    ),
                },
                "save_to": {
                    "type": "string",
                    "description": (
                        "Optional absolute path to write the PNG to. "
                        "When set, the path is also returned in the "
                        "text block alongside the image."
                    ),
                },
            },
        },
    },
    {
        "name": "get_screen_size",
        "description": "Return the primary display's size as ``{width, height}``.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mouse_move",
        "description": "Move the cursor to ``(x, y)`` in screen coordinates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "duration": {
                    "type": "number",
                    "description": "Seconds to animate the move (default 0 = instant).",
                    "default": 0.0,
                },
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "mouse_click",
        "description": "Click at ``(x, y)``. Defaults: left button, 1 click.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "button": {
                    "type": "string",
                    "enum": ["left", "middle", "right"],
                    "default": "left",
                },
                "clicks": {"type": "integer", "default": 1, "minimum": 1, "maximum": 5},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "mouse_scroll",
        "description": (
            "Scroll vertically by ``dy`` clicks (positive = up). If "
            "``x``/``y`` are provided the cursor is moved there first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dy": {"type": "integer"},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
            },
            "required": ["dy"],
        },
    },
    {
        "name": "keyboard_type",
        "description": (
            "Type a string character-by-character through the OS keyboard buffer. Honours the active keyboard layout."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "interval": {
                    "type": "number",
                    "description": "Seconds between keystrokes (default 0).",
                    "default": 0.0,
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "keyboard_press",
        "description": (
            "Press a hotkey combination, e.g. ``ctrl+c``, ``cmd+v``, "
            "``enter``, ``escape``. Multi-key combos use ``+`` as the "
            "separator."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "string",
                    "description": "Hotkey string — single key or ``+``-joined combo.",
                },
            },
            "required": ["keys"],
        },
    },
]


# ----------------------------------------------------------------------- #
#  Tool implementations
# ----------------------------------------------------------------------- #


def _error_content(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _text_content(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": False}


async def _screen_capture(args: dict[str, Any]) -> dict[str, Any]:
    pg, err = _load_pyautogui()
    if pg is None:
        return _error_content(err or "pyautogui unavailable")

    region = args.get("region")
    save_to = args.get("save_to")

    try:
        if region:
            img = pg.screenshot(region=tuple(region))
        else:
            img = pg.screenshot()
    except Exception as exc:  # noqa: BLE001
        return _error_content(f"screen_capture failed: {type(exc).__name__}: {exc}")

    # Encode the PIL image to PNG in memory.
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()
    b64 = base64.b64encode(png_bytes).decode("ascii")

    content: list[dict[str, Any]] = [
        {
            "type": "image",
            "data": b64,
            "mimeType": "image/png",
        }
    ]

    if save_to:
        try:
            path = Path(save_to)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(png_bytes)
            content.append({"type": "text", "text": f"Saved screenshot to {path}"})
        except Exception as exc:  # noqa: BLE001
            # Non-fatal — the image is still in the response.
            content.append(
                {
                    "type": "text",
                    "text": f"Warning: could not write save_to={save_to!r}: {type(exc).__name__}: {exc}",
                }
            )
    else:
        width, height = img.size
        content.append(
            {
                "type": "text",
                "text": f"Captured {width}x{height} PNG ({len(png_bytes)} bytes)",
            }
        )

    return {"content": content, "isError": False}


async def _get_screen_size(args: dict[str, Any]) -> dict[str, Any]:
    pg, err = _load_pyautogui()
    if pg is None:
        return _error_content(err or "pyautogui unavailable")
    try:
        width, height = pg.size()
        return _text_content(json.dumps({"width": int(width), "height": int(height)}))
    except Exception as exc:  # noqa: BLE001
        return _error_content(f"get_screen_size failed: {type(exc).__name__}: {exc}")


async def _mouse_move(args: dict[str, Any]) -> dict[str, Any]:
    pg, err = _load_pyautogui()
    if pg is None:
        return _error_content(err or "pyautogui unavailable")
    try:
        x = int(args["x"])
        y = int(args["y"])
        duration = float(args.get("duration") or 0.0)
        pg.moveTo(x, y, duration=duration)
        return _text_content(f"moved cursor to ({x}, {y})")
    except KeyError as exc:
        return _error_content(f"mouse_move missing required arg: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _error_content(f"mouse_move failed: {type(exc).__name__}: {exc}")


async def _mouse_click(args: dict[str, Any]) -> dict[str, Any]:
    pg, err = _load_pyautogui()
    if pg is None:
        return _error_content(err or "pyautogui unavailable")
    try:
        x = int(args["x"])
        y = int(args["y"])
        button = args.get("button", "left")
        clicks = int(args.get("clicks", 1))
        if button not in ("left", "middle", "right"):
            return _error_content(f"mouse_click: invalid button {button!r}")
        if clicks < 1 or clicks > 5:
            return _error_content(f"mouse_click: clicks must be 1..5, got {clicks}")
        pg.click(x=x, y=y, button=button, clicks=clicks)
        return _text_content(f"clicked {button}x{clicks} at ({x}, {y})")
    except KeyError as exc:
        return _error_content(f"mouse_click missing required arg: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _error_content(f"mouse_click failed: {type(exc).__name__}: {exc}")


async def _mouse_scroll(args: dict[str, Any]) -> dict[str, Any]:
    pg, err = _load_pyautogui()
    if pg is None:
        return _error_content(err or "pyautogui unavailable")
    try:
        dy = int(args["dy"])
        x = args.get("x")
        y = args.get("y")
        if x is not None and y is not None:
            pg.scroll(dy, x=int(x), y=int(y))
            return _text_content(f"scrolled dy={dy} at ({x}, {y})")
        pg.scroll(dy)
        return _text_content(f"scrolled dy={dy}")
    except KeyError as exc:
        return _error_content(f"mouse_scroll missing required arg: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _error_content(f"mouse_scroll failed: {type(exc).__name__}: {exc}")


async def _keyboard_type(args: dict[str, Any]) -> dict[str, Any]:
    pg, err = _load_pyautogui()
    if pg is None:
        return _error_content(err or "pyautogui unavailable")
    try:
        text = args["text"]
        if not isinstance(text, str):
            return _error_content("keyboard_type: 'text' must be a string")
        interval = float(args.get("interval") or 0.0)
        pg.typewrite(text, interval=interval)
        return _text_content(f"typed {len(text)} chars")
    except KeyError as exc:
        return _error_content(f"keyboard_type missing required arg: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _error_content(f"keyboard_type failed: {type(exc).__name__}: {exc}")


async def _keyboard_press(args: dict[str, Any]) -> dict[str, Any]:
    pg, err = _load_pyautogui()
    if pg is None:
        return _error_content(err or "pyautogui unavailable")
    try:
        keys_raw = args["keys"]
        if not isinstance(keys_raw, str) or not keys_raw.strip():
            return _error_content("keyboard_press: 'keys' must be a non-empty string")
        # "+"-delimited combos map onto pyautogui.hotkey(*parts); a single
        # token goes through pyautogui.press() so unrecognised tokens
        # surface as ValueError rather than being silently treated as a
        # single-character press.
        parts = [p.strip().lower() for p in keys_raw.split("+") if p.strip()]
        if len(parts) == 1:
            pg.press(parts[0])
        else:
            pg.hotkey(*parts)
        return _text_content(f"pressed {keys_raw!r}")
    except KeyError as exc:
        return _error_content(f"keyboard_press missing required arg: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _error_content(f"keyboard_press failed: {type(exc).__name__}: {exc}")


_DISPATCH: dict[str, Any] = {
    "screen_capture": _screen_capture,
    "get_screen_size": _get_screen_size,
    "mouse_move": _mouse_move,
    "mouse_click": _mouse_click,
    "mouse_scroll": _mouse_scroll,
    "keyboard_type": _keyboard_type,
    "keyboard_press": _keyboard_press,
}


# ----------------------------------------------------------------------- #
#  JSON-RPC dispatch — mirrors karna.mcp_server.server
# ----------------------------------------------------------------------- #


def _make_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _make_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


async def _handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}

    if req_id is None:
        if method == "notifications/initialized":
            logger.info("client sent initialized notification")
        elif method == "notifications/cancelled":
            logger.info("client requested cancellation: %s", params)
        return None

    if method == "initialize":
        return _make_result(
            req_id,
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
            },
        )

    if method == "ping":
        return _make_result(req_id, {})

    if method == "tools/list":
        return _make_result(req_id, {"tools": _TOOLS})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = _DISPATCH.get(name)
        if handler is None:
            return _make_error(req_id, -32602, f"Unknown tool: {name!r}")
        try:
            result = await handler(args)
        except Exception as exc:  # noqa: BLE001 - surface to client
            logger.exception("tool %s failed", name)
            return _make_result(
                req_id,
                {
                    "content": [{"type": "text", "text": f"[error] {type(exc).__name__}: {exc}"}],
                    "isError": True,
                },
            )
        return _make_result(req_id, result)

    if method in ("shutdown", "exit"):
        return _make_result(req_id, {})

    return _make_error(req_id, -32601, f"Method not found: {method!r}")


# ----------------------------------------------------------------------- #
#  stdio transport
# ----------------------------------------------------------------------- #


async def _serve_stdio() -> None:
    loop = asyncio.get_event_loop()

    def _write(msg: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()

    shutdown_requested = False
    while not shutdown_requested:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if line == "":
            break
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(_make_error(None, -32700, f"Parse error: {exc}"))
            continue
        if message.get("method") in ("shutdown", "exit"):
            shutdown_requested = True
        try:
            response = await _handle_request(message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("dispatch error")
            response = _make_error(message.get("id"), -32603, f"Internal error: {exc}")
        if response is not None:
            _write(response)


def serve() -> None:
    """Synchronous entrypoint — run the stdio server until EOF or shutdown."""
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="[mcp-cc] %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_serve_stdio())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":  # pragma: no cover
    serve()
