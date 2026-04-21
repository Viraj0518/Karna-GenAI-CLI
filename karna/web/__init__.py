"""Web UI for Nellie — FastAPI + Jinja2 + htmx.

A lightweight browser-based interface served alongside the REST API.
Provides session management, live transcript streaming, recipe browsing,
and a memory browser.

Launch::

    nellie web
    # -> http://127.0.0.1:3030
"""

from __future__ import annotations

from karna.web.app import create_web_app

__all__ = ["create_web_app"]
