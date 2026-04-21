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
        "the agent's final text reply plus an optional event trace."
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
            "workspace": {
                "type": "string",
                "description": (
                    "Absolute path to the directory the agent should "
                    "treat as its working directory. When set, bash's "
                    "cwd + write/edit's allowed_roots are pinned here, "
                    "so the agent can create/modify files inside this "
                    "path even if the MCP server was launched elsewhere. "
                    "Created if missing. Defaults to the MCP server's "
                    "own cwd."
                ),
            },
            "include_events": {
                "type": "boolean",
                "description": (
                    "If true, the response carries a compact event "
                    "trace (tool calls, errors, halt reason) alongside "
                    "the final text so the caller can see what the "
                    "agent actually did. Default false."
                ),
                "default": False,
            },
        },
        "required": ["prompt"],
    },
}


# ----------------------------------------------------------------------- #
#  Tool factory with optional workspace scoping
# ----------------------------------------------------------------------- #


def _instantiate_tools(workspace: str | None) -> list:
    """Instantiate every registered tool, scoped to ``workspace`` if given.

    ``write`` and ``edit`` take an optional ``allowed_roots`` kwarg that
    gates their path-safety check; passing the workspace lets the agent
    create files outside the MCP server's own cwd. ``bash`` tracks its
    own ``_cwd`` attribute which we overwrite after construction.
    """
    tools = []
    allowed_roots = [Path(workspace)] if workspace else None
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
#  Agent invocation
# ----------------------------------------------------------------------- #


async def _run_nellie_agent(
    prompt: str,
    *,
    model: str | None = None,
    max_iterations: int = 25,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Drive one turn of the agent loop and return ``{text, events, halt}``.

    - ``text`` is the concatenated assistant text (stripped). Empty if
      the agent never emitted real text — detected and surfaced as an
      error rather than silently returning success.
    - ``events`` is a compact trace: each tool call + result, each
      error, and the terminal reason. Callers can opt in via
      ``include_events``; always collected so the server can decide
      whether to return it.
    - ``halt`` describes why the loop ended: ``done`` | ``error`` |
      ``empty_reply`` | ``max_iterations``. Prevents the "10-minute
      silent success" failure mode.
    """
    config = load_config()

    model_spec = model or f"{config.active_provider}:{config.active_model}"
    provider_name, model_name = resolve_model(model_spec)
    provider = get_provider(provider_name)
    provider.model = model_name

    if workspace:
        os.makedirs(workspace, exist_ok=True)

    tools = _instantiate_tools(workspace)
    conversation = Conversation()
    conversation.messages.append(Message(role="user", content=prompt))

    system_prompt = build_system_prompt(config, tools)
    if workspace:
        # Without this, the model doesn't know the workspace exists and
        # keeps trying to write to the MCP server's launch cwd (which is
        # then rejected by allowed_roots, burning iterations on retries).
        # Tell it explicitly. Normalise to forward slashes for a
        # cross-shell-friendly display (both PowerShell and Git Bash
        # accept forward slashes on Windows).
        ws_display = str(Path(workspace)).replace("\\", "/")
        system_prompt += (
            f"\n\n## Workspace\n"
            f"Your working directory is `{ws_display}`. All bash commands "
            f"run with that cwd. File writes via the write/edit tools "
            f"must stay inside this directory — anything outside is "
            f"rejected by the path-safety guard. Prefer relative paths "
            f"in tool calls (e.g. ``scraper.py`` not "
            f"``C:/Users/.../karna-poc/scraper.py``)."
        )

    text_parts: list[str] = []
    error_parts: list[str] = []
    events: list[dict[str, Any]] = []
    saw_done = False
    # Track tool calls by id so the ``tool_result`` event (emitted by
    # agent_loop after _execute_tool_calls) can be stitched onto the
    # matching tool_call entry in the trace.
    call_index_by_id: dict[str, int] = {}

    async for event in agent_loop(
        provider=provider,
        conversation=conversation,
        tools=tools,
        system_prompt=system_prompt,
        max_iterations=max_iterations,
    ):
        et = event.type
        if et == "text" and event.text:
            text_parts.append(event.text)
        elif et == "tool_call_end" and event.tool_call:
            # Capture at *end* so streaming arguments have finished
            # assembling. Capturing at tool_call_start reads an empty
            # arguments string because deltas haven't arrived yet.
            tc = event.tool_call
            entry = {
                "kind": "tool_call",
                "id": tc.id,
                "name": tc.name,
                "arguments": tc.arguments if isinstance(tc.arguments, dict) else str(tc.arguments)[:1000],
                "result": None,
                "is_error": None,
            }
            call_index_by_id[tc.id] = len(events)
            events.append(entry)
        elif et == "tool_result" and event.tool_result:
            tr = event.tool_result
            # Stitch onto the matching tool_call by id. If the id is
            # missing (older providers don't supply one) append a
            # standalone result entry.
            idx = call_index_by_id.get(tr.tool_call_id)
            if idx is not None:
                events[idx]["result"] = str(tr.content)[:1500]
                events[idx]["is_error"] = bool(tr.is_error)
            else:
                events.append({
                    "kind": "tool_result",
                    "content": str(tr.content)[:1500],
                    "is_error": bool(tr.is_error),
                })
        elif et == "error":
            err = event.error or event.text
            if err:
                error_parts.append(err)
                events.append({"kind": "error", "text": err[:500]})
        elif et == "done":
            saw_done = True

    text = "".join(text_parts).strip()

    if error_parts and not text:
        halt = "error"
    elif not text and not saw_done:
        halt = "max_iterations"
    elif not text:
        halt = "empty_reply"
    else:
        halt = "done"

    events.append({"kind": "halt", "reason": halt})
    return {"text": text, "events": events, "halt": halt, "errors": error_parts}


def _render_transcript(events: list[dict[str, Any]], text: str, halt: str) -> str:
    """Render an agent run as a Claude-Code-style transcript.

    Example output::

        ● Bash(pip install fastapi uvicorn -q)
          ⎿  Successfully installed fastapi-0.118 uvicorn-0.37

        ● Write(api.py)
          ⎿  File created successfully at: C:/Users/12066/karna-poc/api.py

        ● Read(api.py)
          ⎿  [file content: 45 lines]

        Done: built a FastAPI service...
    """
    lines: list[str] = []
    for ev in events:
        kind = ev.get("kind")
        if kind == "tool_call":
            name = ev.get("name", "tool")
            args = ev.get("arguments") or {}
            summary = _tool_arg_summary(name, args)
            bullet = "●" if not ev.get("is_error") else "✗"
            lines.append(f"{bullet} {name.capitalize()}({summary})")
            result = ev.get("result")
            if result:
                # Trim + indent each result line under the ⎿ glyph.
                rlines = str(result).rstrip().splitlines()
                if rlines:
                    lines.append(f"  ⎿  {rlines[0][:200]}")
                    for r in rlines[1:4]:  # cap at 4 lines
                        lines.append(f"     {r[:200]}")
                    if len(rlines) > 4:
                        lines.append(f"     ... ({len(rlines) - 4} more lines)")
            lines.append("")
        elif kind == "error":
            lines.append(f"✗ error: {ev.get('text', '')[:200]}")
            lines.append("")
        elif kind == "halt":
            reason = ev.get("reason", "")
            if reason != "done":
                lines.append(f"[halt: {reason}]")

    if text:
        lines.append(text.strip())
    return "\n".join(lines).rstrip()


def _tool_arg_summary(tool_name: str, args: Any) -> str:
    """One-line summary of a tool's args for the transcript header.

    Mirrors Claude Code's ``Bash(command)`` / ``Write(file_path)`` format.
    """
    if not isinstance(args, dict):
        return str(args)[:80]
    key_priority = {
        "bash": ["command"],
        "read": ["file_path", "path"],
        "write": ["file_path"],
        "edit": ["file_path"],
        "grep": ["pattern"],
        "glob": ["pattern"],
        "web_fetch": ["url"],
        "web_search": ["query"],
        "db": ["sql", "action"],
        "browser": ["url", "action"],
        "notebook": ["path", "action"],
        "document": ["file_path", "path"],
        "comms": ["action"],
        "git": ["action"],
    }
    for key in key_priority.get(tool_name, []):
        if key in args and args[key]:
            return str(args[key])[:100]
    # Fallback: first populated value
    for v in args.values():
        if v:
            return str(v)[:80]
    return ""


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
        workspace = args.get("workspace")
        include_events = bool(args.get("include_events", False))
        try:
            result = await _run_nellie_agent(
                prompt,
                model=model,
                max_iterations=max_iterations,
                workspace=workspace,
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

        halt = result["halt"]
        text = result["text"]
        errors = result["errors"]
        is_error = halt in ("error", "max_iterations", "empty_reply")

        # Claude-Code-style transcript is the primary content block
        # when events are requested. Shows tool calls + their results
        # + final text, so the caller sees what the agent actually did.
        if include_events:
            primary = _render_transcript(result["events"], text, halt)
        else:
            if is_error:
                parts = []
                if text:
                    parts.append(text)
                parts.append(f"[halt:{halt}]")
                if errors:
                    parts.append("errors: " + "; ".join(errors[-3:]))
                primary = "\n".join(parts)
            else:
                primary = text

        content: list[dict[str, Any]] = [
            {"type": "text", "text": primary or "(no reply)"}
        ]
        if include_events:
            # Also attach the raw JSON event trace for programmatic
            # consumers. Keeps the rendered transcript clean while
            # preserving full fidelity for tooling.
            content.append(
                {
                    "type": "text",
                    "text": "RAW_EVENTS:\n" + json.dumps(result["events"], indent=2, default=str),
                }
            )
        return _make_result(req_id, {"content": content, "isError": is_error})

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
