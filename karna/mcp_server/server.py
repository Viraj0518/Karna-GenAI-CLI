"""JSON-RPC 2.0 over stdio server exposing Nellie as an MCP tool.

Protocol methods implemented:
- ``initialize`` — handshake, declares protocolVersion + serverInfo.
- ``tools/list`` — returns the ``nellie_agent`` tool schema.
- ``tools/call`` — runs the agent loop with the given prompt.
- ``ping`` — liveness check.
- ``shutdown`` / ``exit`` — graceful close (MCP convention).

Notifications (no response sent):
- ``notifications/initialized`` — client ack after handshake.
- ``notifications/cancelled`` — client asks to cancel a running call.

Everything else returns JSON-RPC error ``-32601 Method not found``.

The server is stateless across calls: each ``tools/call`` invocation
builds a fresh :class:`Conversation`, streams the agent loop to
completion, and returns the concatenated text reply. No session
persistence on this surface — that belongs to the interactive TUI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from karna.agents.loop import agent_loop
from karna.config import load_config
from karna.models import Conversation, Message
from karna.prompts import build_system_prompt
from karna.providers import get_provider, resolve_model
from karna.tools import get_all_tools

logger = logging.getLogger(__name__)

_SERVER_NAME = "nellie"
_SERVER_VERSION = "0.1.0"
# Match the MCP spec version the client in karna/tools/mcp.py negotiates
# against; bump in lockstep if we upgrade either side.
_PROTOCOL_VERSION = "2024-11-05"


# ----------------------------------------------------------------------- #
#  Tool schema
# ----------------------------------------------------------------------- #

_NELLIE_AGENT_TOOL: dict[str, Any] = {
    "name": "nellie_agent",
    "description": (
        "Spawn a Nellie agent to handle a prompt end-to-end. The agent "
        "has full access to Nellie's tool registry (bash, read/write, "
        "grep/glob, git, web_search/web_fetch, db, browser, etc.) and "
        "runs the same tool-use loop as the interactive REPL. Returns "
        "the agent's final text reply once the loop terminates."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The user prompt to feed the agent.",
            },
            "model": {
                "type": "string",
                "description": (
                    "Optional provider:model override "
                    "(e.g. ``openrouter:qwen/qwen3-coder``). Defaults to "
                    "the configured active model."
                ),
            },
            "max_iterations": {
                "type": "integer",
                "description": "Maximum tool-use iterations (default 25).",
                "default": 25,
            },
        },
        "required": ["prompt"],
    },
}


# ----------------------------------------------------------------------- #
#  Agent invocation
# ----------------------------------------------------------------------- #


async def _run_nellie_agent(
    prompt: str,
    *,
    model: str | None = None,
    max_iterations: int = 25,
) -> str:
    """Drive one turn of the agent loop on ``prompt`` and return its text."""
    config = load_config()

    model_spec = model or f"{config.active_provider}:{config.active_model}"
    provider_name, model_name = resolve_model(model_spec)
    provider = get_provider(provider_name)
    provider.model = model_name

    tools = get_all_tools()
    conversation = Conversation()
    conversation.messages.append(Message(role="user", content=prompt))

    system_prompt = build_system_prompt(config, tools)

    text_parts: list[str] = []
    error_parts: list[str] = []
    async for event in agent_loop(
        provider=provider,
        conversation=conversation,
        tools=tools,
        system_prompt=system_prompt,
        max_iterations=max_iterations,
    ):
        if event.type == "text" and event.text:
            text_parts.append(event.text)
        elif event.type == "error":
            # StreamEvent carries the message in the ``error`` field,
            # not ``text``. The original check on event.text silently
            # dropped provider/auth/max-iter failures and made the
            # tool return "(no reply)" with isError=false — flagged
            # by Codex as P1.
            err = event.error or event.text
            if err:
                error_parts.append(err)

    if error_parts and not text_parts:
        raise RuntimeError("; ".join(error_parts))
    return "".join(text_parts)


# ----------------------------------------------------------------------- #
#  JSON-RPC dispatch
# ----------------------------------------------------------------------- #


def _make_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def _make_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": result,
    }


async def _handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    """Route one JSON-RPC request; return response or None for notifications."""
    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}

    # Notifications (no id) — handle side-effects, no response.
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
        return _make_result(req_id, {"tools": [_NELLIE_AGENT_TOOL]})

    if method == "tools/call":
        name = params.get("name")
        if name != "nellie_agent":
            return _make_error(req_id, -32602, f"Unknown tool: {name!r}")
        args = params.get("arguments") or {}
        prompt = args.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return _make_error(req_id, -32602, "'prompt' must be a non-empty string")
        model = args.get("model")
        max_iterations = int(args.get("max_iterations") or 25)
        try:
            text = await _run_nellie_agent(
                prompt, model=model, max_iterations=max_iterations
            )
        except Exception as exc:  # noqa: BLE001 - surface to client
            logger.exception("nellie_agent call failed")
            return _make_result(
                req_id,
                {
                    "content": [
                        {"type": "text", "text": f"[error] {type(exc).__name__}: {exc}"}
                    ],
                    "isError": True,
                },
            )
        return _make_result(
            req_id,
            {
                "content": [{"type": "text", "text": text or "(no reply)"}],
                "isError": False,
            },
        )

    if method in ("shutdown", "exit"):
        return _make_result(req_id, {})

    return _make_error(req_id, -32601, f"Method not found: {method!r}")


# ----------------------------------------------------------------------- #
#  stdio transport
# ----------------------------------------------------------------------- #


async def _serve_stdio() -> None:
    """Read line-delimited JSON-RPC from stdin; write responses to stdout.

    Uses ``run_in_executor`` for the blocking ``sys.stdin.readline()``
    call so the asyncio loop stays free for the handler coroutine.
    ``asyncio.connect_read_pipe(sys.stdin)`` doesn't work on Windows
    (ProactorEventLoop has no pipe-reader for stdin); this thread-pumped
    path is cross-platform.
    """
    loop = asyncio.get_event_loop()

    def _write(msg: dict[str, Any]) -> None:
        # Anything we write mid-turn — including diagnostics — has to
        # be valid JSON-RPC on stdout; stray prints corrupt the protocol.
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()

    shutdown_requested = False
    while not shutdown_requested:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if line == "":
            break  # EOF
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
            response = _make_error(
                message.get("id"), -32603, f"Internal error: {exc}"
            )
        if response is not None:
            _write(response)


def serve() -> None:
    """Synchronous entrypoint — run the stdio server until EOF or shutdown."""
    # Route all logging to stderr so it doesn't corrupt the stdout JSON-RPC
    # stream. Keep the level conservative so the client isn't flooded.
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="[mcp-server] %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_serve_stdio())
    except KeyboardInterrupt:
        pass
