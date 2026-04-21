"""MCP server wrapper — exposes Nellie's agent loop over JSON-RPC/stdio.

External MCP clients (Claude Code, Cursor, another Nellie via
``karna.tools.mcp``) can invoke ``nellie_agent(prompt, model?)`` as an
MCP tool and receive the agent's final text reply. Under the hood the
call drives the same ``agent_loop`` used by the interactive REPL, with
the same tool registry, auth, and system prompt.

Usage::

    nellie mcp serve

or in an MCP client's server config::

    {"command": "nellie", "args": ["mcp", "serve"]}

The server speaks JSON-RPC 2.0 line-delimited on stdin/stdout, matching
the inverse of :mod:`karna.tools.mcp` (the client).
"""

from __future__ import annotations

from karna.mcp_server.server import serve

__all__ = ["serve"]
