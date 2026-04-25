"""Page routes for the Nellie web UI.

Each route renders a Jinja2 template and fetches data from the REST API
via the internal FastAPI test client (same-process, zero-cost HTTP).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)


def _get_recipe_dirs() -> list[Path]:
    """Return recipe directories to scan (global + project-local)."""
    dirs = []
    global_dir = Path.home() / ".karna" / "recipes"
    if global_dir.exists():
        dirs.append(global_dir)
    # Project-local recipes
    local_dir = Path.cwd() / ".karna" / "recipes"
    if local_dir.exists():
        dirs.append(local_dir)
    return dirs


def _load_recipes() -> list[dict[str, Any]]:
    """Load all recipes from known directories."""
    recipes: list[dict[str, Any]] = []
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return recipes

    for recipe_dir in _get_recipe_dirs():
        for fp in sorted(recipe_dir.glob("*.yaml")) + sorted(recipe_dir.glob("*.yml")):
            try:
                with fp.open(encoding="utf-8") as fh:
                    raw = yaml.safe_load(fh)
                if isinstance(raw, dict) and "name" in raw:
                    recipes.append(
                        {
                            "name": raw["name"],
                            "description": raw.get("description", ""),
                            "path": str(fp),
                            "parameters": raw.get("parameters", []),
                            "model": raw.get("model"),
                            "extensions": raw.get("extensions", []),
                        }
                    )
            except Exception:
                continue
    return recipes


def _load_memories() -> list[dict[str, Any]]:
    """Load all memory entries."""
    from karna.memory.manager import MemoryManager

    mgr = MemoryManager()
    entries = mgr.load_all()
    return [
        {
            "name": e.name,
            "description": e.description,
            "type": e.type,
            "content": e.content,
            "created_at": e.created_at.strftime("%Y-%m-%d %H:%M"),
            "updated_at": e.updated_at.strftime("%Y-%m-%d %H:%M"),
            "file_path": str(e.file_path),
        }
        for e in entries
    ]


def create_router(templates: Jinja2Templates, rest_app: FastAPI) -> APIRouter:
    """Create the page router with template rendering."""
    from fastapi.testclient import TestClient

    router = APIRouter()

    # Internal client for same-process REST calls
    _rest_client = TestClient(rest_app, raise_server_exceptions=False)

    # ------------------------------------------------------------------ #
    #  Index — session list
    # ------------------------------------------------------------------ #

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """Session list page."""
        resp = _rest_client.get("/v1/sessions")
        sessions = resp.json().get("sessions", []) if resp.status_code == 200 else []
        # Enrich sessions with preview info
        for s in sessions:
            s["message_preview"] = ""
            if s.get("message_count", 0) > 0:
                detail = _rest_client.get(f"/v1/sessions/{s['id']}")
                if detail.status_code == 200:
                    msgs = detail.json().get("messages", [])
                    user_msgs = [m for m in msgs if m["role"] == "user"]
                    if user_msgs:
                        s["message_preview"] = user_msgs[0]["content"][:80]
        return templates.TemplateResponse(request, "index.html", context={"sessions": sessions})

    # ------------------------------------------------------------------ #
    #  New session
    # ------------------------------------------------------------------ #

    @router.post("/sessions/new", response_class=HTMLResponse)
    async def session_create(request: Request):
        """Create a new session and redirect to it."""
        resp = _rest_client.post("/v1/sessions", json={})
        if resp.status_code == 200:
            sid = resp.json()["id"]
            return RedirectResponse(url=f"/sessions/{sid}", status_code=303)
        return RedirectResponse(url="/", status_code=303)

    # ------------------------------------------------------------------ #
    #  Session detail — live transcript
    # ------------------------------------------------------------------ #

    @router.get("/sessions/{sid}", response_class=HTMLResponse)
    async def session_detail(request: Request, sid: str):
        """Live transcript page for a session."""
        resp = _rest_client.get(f"/v1/sessions/{sid}")
        if resp.status_code != 200:
            return templates.TemplateResponse(
                request,
                "index.html",
                context={"sessions": [], "error": "Session not found"},
                status_code=404,
            )
        session = resp.json()
        return templates.TemplateResponse(request, "session.html", context={"session": session})

    # ------------------------------------------------------------------ #
    #  Send message (htmx POST)
    # ------------------------------------------------------------------ #

    @router.post("/sessions/{sid}/send", response_class=HTMLResponse)
    async def session_send(request: Request, sid: str, content: str = Form(...)):
        """Send a message to the session (htmx partial)."""
        resp = _rest_client.post(
            f"/v1/sessions/{sid}/messages",
            json={"content": content},
        )
        if resp.status_code == 200:
            result = resp.json()
            # Return the updated messages as HTML partial
            detail = _rest_client.get(f"/v1/sessions/{sid}")
            session = detail.json() if detail.status_code == 200 else {"messages": []}
            return templates.TemplateResponse(
                request,
                "partials/messages.html",
                context={"session": session, "result": result},
            )
        return HTMLResponse(
            f'<div class="error">Error: {resp.status_code}</div>',
            status_code=resp.status_code,
        )

    # ------------------------------------------------------------------ #
    #  SSE proxy — streams events from the REST API
    # ------------------------------------------------------------------ #

    @router.get("/sessions/{sid}/stream")
    async def session_stream(sid: str):
        """Proxy SSE stream from the REST API."""

        async def event_generator():
            # Connect to the internal REST SSE endpoint
            resp = _rest_client.get(f"/v1/sessions/{sid}")
            if resp.status_code != 200:
                yield f"data: {json.dumps({'kind': 'error', 'text': 'session not found'})}\n\n"
                return

            # Stream from the REST SSE endpoint
            with _rest_client.stream("GET", f"/v1/sessions/{sid}/events") as stream:
                for line in stream.iter_lines():
                    if line.startswith("data: "):
                        yield f"{line}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
        )

    # ------------------------------------------------------------------ #
    #  Cancel (htmx POST)
    # ------------------------------------------------------------------ #

    @router.post("/sessions/{sid}/cancel", response_class=HTMLResponse)
    async def session_cancel(request: Request, sid: str):
        """Cancel the running turn."""
        return HTMLResponse('<div class="status">Cancelled</div>')

    # ------------------------------------------------------------------ #
    #  Delete session
    # ------------------------------------------------------------------ #

    @router.post("/sessions/{sid}/delete")
    async def session_delete(sid: str):
        """Delete a session and redirect to index."""
        _rest_client.delete(f"/v1/sessions/{sid}")
        return RedirectResponse(url="/", status_code=303)

    # ------------------------------------------------------------------ #
    #  Recipes page
    # ------------------------------------------------------------------ #

    @router.get("/recipes", response_class=HTMLResponse)
    async def recipes_page(request: Request):
        """Recipe library browser."""
        recipes = _load_recipes()
        return templates.TemplateResponse(request, "recipes.html", context={"recipes": recipes})

    @router.post("/recipes/run", response_class=HTMLResponse)
    async def recipe_run(request: Request, recipe_path: str = Form(...)):
        """Run a recipe — create a session and execute it."""
        # Create a session
        resp = _rest_client.post("/v1/sessions", json={})
        if resp.status_code != 200:
            return RedirectResponse(url="/recipes", status_code=303)
        sid = resp.json()["id"]

        # Load the recipe and send its instructions as a message
        try:
            from karna.recipes.loader import load_recipe

            rec = load_recipe(recipe_path)
            _rest_client.post(
                f"/v1/sessions/{sid}/messages",
                json={"content": f"[Recipe: {rec.name}]\n\n{rec.instructions}"},
            )
        except Exception as exc:
            logger.warning("Failed to run recipe %s: %s", recipe_path, exc)

        return RedirectResponse(url=f"/sessions/{sid}", status_code=303)

    # ------------------------------------------------------------------ #
    #  Memory browser
    # ------------------------------------------------------------------ #

    @router.get("/memory", response_class=HTMLResponse)
    async def memory_page(request: Request, type_filter: str = ""):
        """Memory browser page."""
        memories = _load_memories()
        types = sorted({m["type"] for m in memories})
        if type_filter:
            memories = [m for m in memories if m["type"] == type_filter]
        return templates.TemplateResponse(
            request,
            "memory.html",
            context={"memories": memories, "types": types, "type_filter": type_filter},
        )

    @router.post("/memory/create", response_class=HTMLResponse)
    async def memory_create(
        request: Request,
        name: str = Form(...),
        memory_type: str = Form(...),
        description: str = Form(""),
        content: str = Form(...),
    ):
        """Create a new memory."""
        from karna.memory.manager import MemoryManager

        mgr = MemoryManager()
        try:
            mgr.save_memory(name=name, type=memory_type, description=description, content=content)
        except ValueError as exc:
            memories = _load_memories()
            types = sorted({m["type"] for m in memories})
            return templates.TemplateResponse(
                request,
                "memory.html",
                context={"memories": memories, "types": types, "type_filter": "", "error": str(exc)},
            )
        return RedirectResponse(url="/memory", status_code=303)

    @router.post("/memory/delete", response_class=HTMLResponse)
    async def memory_delete(file_path: str = Form(...)):
        """Delete a memory."""
        from karna.memory.manager import MemoryManager

        mgr = MemoryManager()
        fp = Path(file_path)
        if fp.exists():
            mgr.delete_memory(fp)
        return RedirectResponse(url="/memory", status_code=303)

    @router.post("/memory/update", response_class=HTMLResponse)
    async def memory_update(
        file_path: str = Form(...),
        content: str = Form(...),
    ):
        """Update a memory's content."""
        from karna.memory.manager import MemoryManager

        mgr = MemoryManager()
        fp = Path(file_path)
        if fp.exists():
            mgr.update_memory(fp, content)
        return RedirectResponse(url="/memory", status_code=303)

    return router
