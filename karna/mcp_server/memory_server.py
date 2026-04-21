"""JSON-RPC 2.0 over stdio server exposing Nellie's memory system via MCP.

Protocol methods implemented:
- ``initialize`` -- handshake, declares protocolVersion + serverInfo.
- ``tools/list`` -- returns the 4 memory tools.
- ``tools/call`` -- dispatches to memory_list / memory_get / memory_save / memory_delete.
- ``ping`` -- liveness check.
- ``shutdown`` / ``exit`` -- graceful close.

Notifications (no response sent):
- ``notifications/initialized`` -- client ack after handshake.
- ``notifications/cancelled`` -- client asks to cancel.

Everything else returns JSON-RPC error ``-32601 Method not found``.

Usage::

    nellie mcp serve-memory

or in an MCP client config::

    {"command": "nellie", "args": ["mcp", "serve-memory"]}
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

_SERVER_NAME = "nellie-memory"
_SERVER_VERSION = "0.1.0"
_PROTOCOL_VERSION = "2024-11-05"


# ----------------------------------------------------------------------- #
#  Tool schemas
# ----------------------------------------------------------------------- #

_TOOL_MEMORY_LIST: dict[str, Any] = {
    "name": "memory_list",
    "description": (
        "List all memories stored by Nellie.  Returns an array of objects "
        "with fields: name, type, description, age.  Optionally filter by "
        "memory type."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "description": (
                    "Optional memory type filter (e.g. 'user', 'feedback', 'project', 'reference').  Omit to list all."
                ),
            },
        },
    },
}

_TOOL_MEMORY_GET: dict[str, Any] = {
    "name": "memory_get",
    "description": (
        "Retrieve the full content of a specific memory by name.  "
        "Returns the memory's name, type, description, age, and body text."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The name of the memory to retrieve.",
            },
        },
        "required": ["name"],
    },
}

_TOOL_MEMORY_SAVE: dict[str, Any] = {
    "name": "memory_save",
    "description": (
        "Save a new memory.  Requires a name, type, short description, "
        "and the body text.  Valid types are: user, feedback, project, "
        "reference (plus any custom types configured in config.toml)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Human-readable title for the memory.",
            },
            "type": {
                "type": "string",
                "description": ("Memory type: user, feedback, project, or reference."),
            },
            "description": {
                "type": "string",
                "description": "One-line description used for relevance matching.",
            },
            "body": {
                "type": "string",
                "description": "Full memory content (markdown body).",
            },
        },
        "required": ["name", "type", "description", "body"],
    },
}

_TOOL_MEMORY_DELETE: dict[str, Any] = {
    "name": "memory_delete",
    "description": (
        "Delete a memory by name.  Removes the file and its index entry.  "
        "Returns success or an error if the memory is not found."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The name of the memory to delete.",
            },
        },
        "required": ["name"],
    },
}

_ALL_TOOLS = [_TOOL_MEMORY_LIST, _TOOL_MEMORY_GET, _TOOL_MEMORY_SAVE, _TOOL_MEMORY_DELETE]


# ----------------------------------------------------------------------- #
#  MemoryManager factory
# ----------------------------------------------------------------------- #


def _get_memory_manager():
    """Instantiate a MemoryManager from the active config.

    Deferred import so the module can be loaded without triggering
    heavy config/provider initialization at import time.
    """
    from karna.config import load_config
    from karna.memory.manager import MemoryManager

    cfg = load_config()
    return MemoryManager(memory_config=cfg.memory)


# ----------------------------------------------------------------------- #
#  Age helper (mirrors manager._memory_age_text but for MemoryEntry)
# ----------------------------------------------------------------------- #


def _age_text(entry) -> str:
    """Human-readable age string from a MemoryEntry's updated_at."""
    from karna.memory.manager import _memory_age_text

    return _memory_age_text(entry.updated_at.timestamp())


# ----------------------------------------------------------------------- #
#  Tool handlers
# ----------------------------------------------------------------------- #


def _handle_memory_list(args: dict[str, Any]) -> dict[str, Any]:
    """List memories, optionally filtered by type."""
    mgr = _get_memory_manager()
    entries = mgr.load_all()

    type_filter = args.get("type")
    if type_filter:
        entries = [e for e in entries if e.type == type_filter]

    items = []
    for entry in entries:
        items.append(
            {
                "name": entry.name,
                "type": entry.type,
                "description": entry.description,
                "age": _age_text(entry),
            }
        )

    return {
        "content": [{"type": "text", "text": json.dumps(items, indent=2)}],
        "isError": False,
    }


def _handle_memory_get(args: dict[str, Any]) -> dict[str, Any]:
    """Get a single memory by name."""
    name = args.get("name")
    if not name:
        return {
            "content": [{"type": "text", "text": "Error: 'name' is required."}],
            "isError": True,
        }

    mgr = _get_memory_manager()
    entries = mgr.load_all()

    # Find the first entry whose name matches (case-insensitive).
    match = None
    for entry in entries:
        if entry.name.lower() == name.lower():
            match = entry
            break

    if match is None:
        return {
            "content": [{"type": "text", "text": f"Memory not found: {name!r}"}],
            "isError": True,
        }

    result = {
        "name": match.name,
        "type": match.type,
        "description": match.description,
        "age": _age_text(match),
        "content": match.content,
    }
    return {
        "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
        "isError": False,
    }


def _handle_memory_save(args: dict[str, Any]) -> dict[str, Any]:
    """Save a new memory."""
    name = args.get("name")
    mem_type = args.get("type")
    description = args.get("description")
    body = args.get("body")

    missing = []
    if not name:
        missing.append("name")
    if not mem_type:
        missing.append("type")
    if not description:
        missing.append("description")
    if not body:
        missing.append("body")

    if missing:
        return {
            "content": [{"type": "text", "text": f"Missing required fields: {', '.join(missing)}"}],
            "isError": True,
        }

    mgr = _get_memory_manager()
    try:
        fp = mgr.save_memory(name=name, type=mem_type, description=description, content=body)
    except ValueError as exc:
        return {
            "content": [{"type": "text", "text": f"Error: {exc}"}],
            "isError": True,
        }

    return {
        "content": [{"type": "text", "text": json.dumps({"saved": True, "path": str(fp)})}],
        "isError": False,
    }


def _handle_memory_delete(args: dict[str, Any]) -> dict[str, Any]:
    """Delete a memory by name."""
    name = args.get("name")
    if not name:
        return {
            "content": [{"type": "text", "text": "Error: 'name' is required."}],
            "isError": True,
        }

    mgr = _get_memory_manager()
    entries = mgr.load_all()

    match = None
    for entry in entries:
        if entry.name.lower() == name.lower():
            match = entry
            break

    if match is None:
        return {
            "content": [{"type": "text", "text": f"Memory not found: {name!r}"}],
            "isError": True,
        }

    mgr.delete_memory(match.file_path)
    return {
        "content": [{"type": "text", "text": json.dumps({"deleted": True, "name": match.name})}],
        "isError": False,
    }


_TOOL_DISPATCH = {
    "memory_list": _handle_memory_list,
    "memory_get": _handle_memory_get,
    "memory_save": _handle_memory_save,
    "memory_delete": _handle_memory_delete,
}


# ----------------------------------------------------------------------- #
#  JSON-RPC helpers
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


# ----------------------------------------------------------------------- #
#  Request handler
# ----------------------------------------------------------------------- #


def _handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    """Route one JSON-RPC request; return response or None for notifications."""
    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}

    # Notifications (no id) -- handle side-effects, no response.
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
        return _make_result(req_id, {"tools": _ALL_TOOLS})

    if method == "tools/call":
        tool_name = params.get("name")
        handler = _TOOL_DISPATCH.get(tool_name)
        if handler is None:
            return _make_error(req_id, -32602, f"Unknown tool: {tool_name!r}")
        args = params.get("arguments") or {}
        try:
            result = handler(args)
        except Exception as exc:  # noqa: BLE001
            logger.exception("tool %s failed", tool_name)
            result = {
                "content": [{"type": "text", "text": f"Internal error: {exc}"}],
                "isError": True,
            }
        return _make_result(req_id, result)

    if method in ("shutdown", "exit"):
        return _make_result(req_id, {})

    return _make_error(req_id, -32601, f"Method not found: {method!r}")


# ----------------------------------------------------------------------- #
#  stdio transport
# ----------------------------------------------------------------------- #


def _serve_stdio() -> None:
    """Read line-delimited JSON-RPC from stdin; write responses to stdout.

    This server is fully synchronous -- no asyncio needed because the
    memory operations are plain filesystem reads/writes with no I/O
    concurrency requirements.
    """

    def _write(msg: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()

    shutdown_requested = False
    while not shutdown_requested:
        line = sys.stdin.readline()
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
            response = _handle_request(message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("dispatch error")
            response = _make_error(message.get("id"), -32603, f"Internal error: {exc}")
        if response is not None:
            _write(response)


def run_memory_server() -> None:
    """Synchronous entrypoint -- run the memory MCP server until EOF or shutdown."""
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="[mcp-memory] %(levelname)s %(name)s: %(message)s",
    )
    try:
        _serve_stdio()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run_memory_server()
