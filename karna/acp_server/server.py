"""JSON-RPC 2.0 over stdio — ACP server for agent-to-agent invocation.

Agent Client Protocol (ACP) is Goose's protocol for agent↔agent
communication — distinct from MCP which is host↔extension. An ACP
client (e.g. another Nellie, an IDE plugin, a test harness) opens a
session and drives it by sending user prompts + receiving streaming
agent events.

Methods implemented (server-side)::

    initialize          handshake
    session/new         open a new session (workspace, model)
    session/list        list open sessions
    session/prompt      send a user message — server streams updates
                        via session/update notifications, then replies
                        with the final text
    session/cancel      cancel an in-flight prompt on a session
    session/close       release session resources
    shutdown / exit     graceful close

Notifications emitted (server → client, no response)::

    session/update      {session_id, kind: text|tool_call|tool_result|error|done, ...}

Transport is line-delimited JSON on stdio. Stdin-read runs in a
thread executor so the same code works on Windows (ProactorEventLoop
doesn't support connect_read_pipe(sys.stdin)).

This mirrors karna.mcp_server but with ACP's method surface + the
streaming notification shape. Shares the same agent_loop + session
plumbing, so anything we fix in one server benefits both.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from karna.agents.loop import agent_loop
from karna.config import load_config
from karna.models import Conversation, Message
from karna.prompts import build_system_prompt
from karna.providers import get_provider, resolve_model
from karna.tools import _TOOL_PATHS  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)

_SERVER_NAME = "nellie-acp"
_SERVER_VERSION = "0.1.3"
_PROTOCOL_VERSION = "2024-11-05"


# ----------------------------------------------------------------------- #
#  Session state (per-process in-memory)
# ----------------------------------------------------------------------- #


class _Session:
    __slots__ = ("id", "workspace", "model", "conversation", "active_task")

    def __init__(
        self,
        sid: str,
        *,
        workspace: str | None = None,
        model: str | None = None,
    ) -> None:
        self.id = sid
        self.workspace = workspace
        self.model = model
        self.conversation = Conversation()
        self.active_task: asyncio.Task | None = None


_sessions: dict[str, _Session] = {}


def _instantiate_tools(workspace: str | None) -> list:
    """Scope write/edit allowed_roots + bash cwd to a session workspace."""
    allowed_roots = [Path(workspace)] if workspace else None
    tools = []
    for name, (module_path, class_name) in _TOOL_PATHS.items():
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        if allowed_roots and name in ("write", "edit"):
            instance = cls(allowed_roots=allowed_roots)
        else:
            instance = cls()
        if workspace and name == "bash" and hasattr(instance, "_cwd"):
            instance._cwd = workspace  # type: ignore[attr-defined]
        tools.append(instance)
    return tools


# ----------------------------------------------------------------------- #
#  JSON-RPC helpers
# ----------------------------------------------------------------------- #


def _make_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _make_notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


# ----------------------------------------------------------------------- #
#  Agent turn driver — publishes session/update notifications
# ----------------------------------------------------------------------- #


async def _drive_prompt(
    session: _Session,
    prompt_text: str,
    write_notification,
    *,
    max_iterations: int = 25,
) -> dict[str, Any]:
    """Run one agent turn, stream updates as notifications, return summary."""
    config = load_config()
    model_spec = session.model or f"{config.active_provider}:{config.active_model}"
    provider_name, model_name = resolve_model(model_spec)
    provider = get_provider(provider_name)
    provider.model = model_name

    if session.workspace:
        os.makedirs(session.workspace, exist_ok=True)
    tools = _instantiate_tools(session.workspace)
    session.conversation.messages.append(Message(role="user", content=prompt_text))
    system_prompt = build_system_prompt(config, tools)

    def notify(kind: str, **fields: Any) -> None:
        write_notification(
            _make_notification(
                "session/update",
                {"session_id": session.id, "kind": kind, **fields},
            )
        )

    text_parts: list[str] = []
    errors: list[str] = []
    saw_done = False

    try:
        async for event in agent_loop(
            provider=provider,
            conversation=session.conversation,
            tools=tools,
            system_prompt=system_prompt,
            max_iterations=max_iterations,
        ):
            et = event.type
            if et == "text" and event.text:
                text_parts.append(event.text)
                notify("text", delta=event.text)
            elif et == "tool_call_end" and event.tool_call:
                tc = event.tool_call
                notify(
                    "tool_call",
                    id=tc.id,
                    name=tc.name,
                    arguments=tc.arguments if isinstance(tc.arguments, dict) else str(tc.arguments)[:1000],
                )
            elif et == "tool_result" and event.tool_result:
                tr = event.tool_result
                notify(
                    "tool_result",
                    id=tr.tool_call_id,
                    content=str(tr.content)[:2000],
                    is_error=bool(tr.is_error),
                )
            elif et == "error":
                err = event.error or event.text
                if err:
                    errors.append(err)
                    notify("error", text=err[:500])
            elif et == "done":
                saw_done = True
    except asyncio.CancelledError:
        notify("cancelled")
        raise

    text = "".join(text_parts).strip()
    if errors and not text:
        halt = "error"
    elif not text and not saw_done:
        halt = "max_iterations"
    elif not text:
        halt = "empty_reply"
    else:
        halt = "done"

    notify("done", halt=halt, text=text)
    return {"text": text, "halt": halt, "errors": errors}


# ----------------------------------------------------------------------- #
#  Request dispatch
# ----------------------------------------------------------------------- #


async def _handle_request(message: dict[str, Any], write_notification) -> dict[str, Any] | None:
    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}

    # Notifications — no response expected.
    if req_id is None:
        if method == "notifications/initialized":
            logger.info("client sent initialized notification")
        elif method == "notifications/cancelled":
            logger.info("client cancelled: %s", params)
        return None

    if method == "initialize":
        return _make_result(
            req_id,
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"session": {"streaming": True}},
                "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
            },
        )

    if method == "session/new":
        import secrets

        sid = secrets.token_urlsafe(12)
        while sid in _sessions:
            sid = secrets.token_urlsafe(12)
        session = _Session(
            sid,
            workspace=params.get("workspace"),
            model=params.get("model"),
        )
        _sessions[sid] = session
        return _make_result(
            req_id,
            {"session_id": sid, "workspace": session.workspace, "model": session.model},
        )

    if method == "session/list":
        return _make_result(
            req_id,
            {
                "sessions": [
                    {
                        "session_id": s.id,
                        "workspace": s.workspace,
                        "model": s.model,
                        "message_count": len(s.conversation.messages),
                        "active": s.active_task is not None and not s.active_task.done(),
                    }
                    for s in _sessions.values()
                ]
            },
        )

    if method == "session/prompt":
        sid = params.get("session_id")
        prompt_text = params.get("prompt")
        if sid not in _sessions:
            return _make_error(req_id, -32602, f"Unknown session_id: {sid!r}")
        if not isinstance(prompt_text, str) or not prompt_text.strip():
            return _make_error(req_id, -32602, "'prompt' must be a non-empty string")
        session = _sessions[sid]
        if session.active_task is not None and not session.active_task.done():
            return _make_error(req_id, -32002, "Session already has an active prompt")
        max_iters = int(params.get("max_iterations") or 25)

        # Run the agent loop as a task so session/cancel can kill it.
        task = asyncio.create_task(_drive_prompt(session, prompt_text, write_notification, max_iterations=max_iters))
        session.active_task = task
        try:
            result = await task
        except asyncio.CancelledError:
            return _make_result(req_id, {"text": "", "halt": "cancelled"})
        finally:
            session.active_task = None
        return _make_result(req_id, result)

    if method == "session/cancel":
        sid = params.get("session_id")
        if sid not in _sessions:
            return _make_error(req_id, -32602, f"Unknown session_id: {sid!r}")
        session = _sessions[sid]
        if session.active_task and not session.active_task.done():
            session.active_task.cancel()
            return _make_result(req_id, {"cancelled": True})
        return _make_result(req_id, {"cancelled": False, "reason": "no active prompt"})

    if method == "session/close":
        sid = params.get("session_id")
        removed = _sessions.pop(sid, None)
        if removed is None:
            return _make_error(req_id, -32602, f"Unknown session_id: {sid!r}")
        if removed.active_task and not removed.active_task.done():
            removed.active_task.cancel()
        return _make_result(req_id, {"closed": sid})

    if method == "ping":
        return _make_result(req_id, {})

    if method in ("shutdown", "exit"):
        return _make_result(req_id, {})

    return _make_error(req_id, -32601, f"Method not found: {method!r}")


# ----------------------------------------------------------------------- #
#  Stdio transport
# ----------------------------------------------------------------------- #


async def _serve_stdio() -> None:
    loop = asyncio.get_event_loop()

    def write(msg: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()

    shutdown = False
    while not shutdown:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if line == "":
            break
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            write(_make_error(None, -32700, f"Parse error: {exc}"))
            continue

        if message.get("method") in ("shutdown", "exit"):
            shutdown = True

        try:
            response = await _handle_request(message, write)
        except Exception as exc:  # noqa: BLE001
            logger.exception("ACP dispatch error")
            response = _make_error(message.get("id"), -32603, f"Internal error: {exc}")
        if response is not None:
            write(response)


def serve() -> None:
    """Synchronous entrypoint — run stdio server until EOF or shutdown."""
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="[acp-server] %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_serve_stdio())
    except KeyboardInterrupt:
        pass
