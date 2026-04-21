"""REST + SSE server wrapper — exposes Nellie over HTTP.

The ``goosed``-equivalent surface for Nellie. Lets any HTTP client
(desktop app, web UI, external automation) drive the agent loop
with session-scoped state.

Launch::

    nellie serve
    # → uvicorn on :3030 by default

Endpoints::

    GET    /health                        liveness
    GET    /v1/tools                      list registered tools
    POST   /v1/sessions                   create a session
    GET    /v1/sessions                   list sessions
    GET    /v1/sessions/{id}              get session state
    DELETE /v1/sessions/{id}              close session
    POST   /v1/sessions/{id}/messages     send user message, get reply
    GET    /v1/sessions/{id}/events       SSE stream of live events
"""

from __future__ import annotations

from karna.rest_server.app import create_app, serve

__all__ = ["create_app", "serve"]
