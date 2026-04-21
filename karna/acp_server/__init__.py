"""ACP server wrapper — exposes Nellie over the Agent Client Protocol.

ACP is a JSON-RPC 2.0 over stdio protocol for agent↔agent communication
(as distinct from MCP which is host↔extension). An external agent
(client) can issue ``session/new``, ``session/prompt``, and
``session/cancel`` methods and receive streaming ``session/update``
notifications from us (server).

Goose-parity row #17 in ``research/karna/NELLIE_VS_GOOSE_PARITY.md``.

Launch::

    nellie acp serve

or in a client's config::

    {"command": "nellie", "args": ["acp", "serve"]}

See :mod:`karna.acp_server.server` for the protocol surface.
"""

from __future__ import annotations

from karna.acp_server.server import serve

__all__ = ["serve"]
