"""FastAPI app that serves the Nellie web UI with Jinja2 templates.

Mounts the REST API from ``karna.rest_server`` and adds HTML page routes
served via Jinja2 templates + htmx for interactivity.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

_PACKAGE_DIR = Path(__file__).parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"


def create_web_app() -> FastAPI:
    """Build the combined FastAPI app: REST API + web UI pages."""
    from karna.rest_server.app import create_app as create_rest_app

    # Create the REST app first — it has the API routes
    rest_app = create_rest_app()

    # Create the web UI app that wraps the REST app
    app = FastAPI(
        title="Nellie Web UI",
        version="0.1.0",
        description="Browser interface for Nellie's agent loop",
    )

    # Mount static files
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Set up Jinja2 templates
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    # Import and include routes
    from karna.web.routes import create_router

    router = create_router(templates, rest_app)
    app.include_router(router)

    # Mount the REST API under /api so web UI can proxy to it
    app.mount("/api", rest_app)

    return app


def serve_web(host: str = "127.0.0.1", port: int = 3030) -> None:
    """CLI entrypoint - launch uvicorn with the web UI app."""
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("uvicorn not installed. Install the REST server extra: pip install 'karna[rest]'") from exc

    logging.basicConfig(
        level=logging.INFO,
        format="[nellie-web] %(levelname)s %(name)s: %(message)s",
    )

    import webbrowser

    url = f"http://{host}:{port}"
    logger.info("Nellie Web UI starting at %s", url)

    # Open browser after a short delay (uvicorn needs to bind first)
    import threading

    def _open_browser() -> None:
        import time

        time.sleep(1.0)
        webbrowser.open(url)

    threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(create_web_app(), host=host, port=port, log_level="info")
