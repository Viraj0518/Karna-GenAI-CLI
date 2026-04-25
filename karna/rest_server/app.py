"""FastAPI app + lifecycle hook that exposes Nellie over HTTP.

Mirrors the MCP server's tool surface as REST for clients that can't
speak JSON-RPC/stdio (web UIs, external automation, CI tools). Shares
no state with the MCP stdio server — each process is independent.

Streaming: ``/v1/sessions/{id}/events`` is a Server-Sent Events
endpoint. A client can connect once and receive live ``tool_call``,
``tool_result``, ``text`` and ``done`` events for the session's
running turn, Claude-Code-style.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator

try:  # pragma: no cover — FastAPI is an optional extra
    from fastapi import WebSocket as _FastAPIWebSocket
except ImportError:  # pragma: no cover
    _FastAPIWebSocket = None  # type: ignore[misc,assignment]

from karna.agents.loop import agent_loop
from karna.config import load_config
from karna.models import Message
from karna.prompts import build_system_prompt
from karna.providers import get_provider, resolve_model
from karna.rest_server.session_manager import Session, SessionManager
from karna.tools import _TOOL_PATHS  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)

_SERVER_NAME = "nellie-rest"
_SERVER_VERSION = "0.1.3"


# ----------------------------------------------------------------------- #
#  Tool instantiation (scoped to a session's workspace if set)
# ----------------------------------------------------------------------- #


def _instantiate_tools(workspace: str | None) -> list:
    """Build one instance of every registered tool, scoped to workspace.

    Same pattern as ``karna/mcp_server/server.py::_instantiate_tools``.
    Kept duplicated deliberately — the two servers are independent; if
    one's tool factory changes, the other shouldn't silently follow.
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
#  Agent turn runner — publishes events to the session's SSE queue
# ----------------------------------------------------------------------- #


async def _run_turn(
    session: Session,
    user_content: str,
    *,
    max_iterations: int = 25,
) -> dict[str, Any]:
    """Run one agent turn, push events to SSE queue, return final summary."""
    config = load_config()
    model_spec = session.model or f"{config.active_provider}:{config.active_model}"
    provider_name, model_name = resolve_model(model_spec)
    provider = get_provider(provider_name)
    provider.model = model_name

    tools = _instantiate_tools(session.workspace)
    session.conversation.messages.append(Message(role="user", content=user_content))
    system_prompt = build_system_prompt(config, tools)

    text_parts: list[str] = []
    errors: list[str] = []
    call_index: dict[str, int] = {}
    events_emitted: list[dict[str, Any]] = []
    saw_done = False

    async def push(event: dict[str, Any]) -> None:
        events_emitted.append(event)
        try:
            session.event_queue.put_nowait(event)
        except asyncio.QueueFull:
            # Drop oldest; a slow consumer shouldn't stall the agent.
            try:
                session.event_queue.get_nowait()
                session.event_queue.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

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
            await push({"kind": "text", "delta": event.text})
        elif et == "tool_call_end" and event.tool_call:
            tc = event.tool_call
            call_index[tc.id] = len(events_emitted)
            await push(
                {
                    "kind": "tool_call",
                    "id": tc.id,
                    "name": tc.name,
                    "arguments": tc.arguments if isinstance(tc.arguments, dict) else str(tc.arguments)[:1000],
                }
            )
        elif et == "tool_result" and event.tool_result:
            tr = event.tool_result
            await push(
                {
                    "kind": "tool_result",
                    "id": tr.tool_call_id,
                    "content": str(tr.content)[:2000],
                    "is_error": bool(tr.is_error),
                }
            )
        elif et == "error":
            err = event.error or event.text
            if err:
                errors.append(err)
                await push({"kind": "error", "text": err[:500]})
        elif et == "done":
            saw_done = True

    text = "".join(text_parts).strip()
    if errors and not text:
        halt = "error"
    elif not text and not saw_done:
        halt = "max_iterations"
    elif not text:
        halt = "empty_reply"
    else:
        halt = "done"

    await push({"kind": "done", "halt": halt, "text": text})
    return {"text": text, "halt": halt, "errors": errors, "event_count": len(events_emitted)}


# ----------------------------------------------------------------------- #
#  FastAPI app
# ----------------------------------------------------------------------- #


def create_app():
    """Build the FastAPI app. Imported lazily so ``karna`` works without
    FastAPI installed (it's an optional extra)."""
    from fastapi import Body, FastAPI, HTTPException
    from fastapi.responses import StreamingResponse

    # Per-app SessionManager captured in closure. Avoids FastAPI's
    # Request-injection machinery (which mis-parses the parameter on
    # some pydantic/fastapi combinations) and keeps the manager out
    # of the request signature entirely.
    sm = SessionManager()
    logger.info("nellie REST server session manager initialised")

    app = FastAPI(
        title="Nellie",
        version=_SERVER_VERSION,
        description="REST + SSE wrapper around Nellie's agent loop",
    )

    @app.get("/health")
    async def health():
        return {"status": "ok", "server": _SERVER_NAME, "version": _SERVER_VERSION}

    @app.get("/v1/tools")
    async def tools_list():
        """List every registered tool + its description."""
        out = []
        for name, (module_path, class_name) in _TOOL_PATHS.items():
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            out.append(
                {
                    "name": name,
                    "class": f"{module_path}:{class_name}",
                    "description": getattr(cls, "description", ""),
                }
            )
        return {"tools": out}

    @app.post("/v1/sessions")
    async def session_create(
        payload: dict = Body(default_factory=dict),
    ):
        workspace = payload.get("workspace")
        model = payload.get("model")
        system = payload.get("system_instructions")
        session = await sm.create(
            workspace=workspace,
            model=model,
            system_instructions=system,
        )
        return {
            "id": session.id,
            "workspace": session.workspace,
            "model": session.model,
            "created_at": session.created_at,
        }

    @app.get("/v1/sessions")
    async def session_list():
        return {
            "sessions": [
                {
                    "id": s.id,
                    "workspace": s.workspace,
                    "model": s.model,
                    "created_at": s.created_at,
                    "last_activity": s.last_activity,
                    "message_count": len(s.conversation.messages),
                }
                for s in sm.list()
            ]
        }

    @app.get("/v1/sessions/{sid}")
    async def session_get(sid: str):
        s = sm.get(sid)
        if s is None:
            raise HTTPException(status_code=404, detail="session not found")
        return {
            "id": s.id,
            "workspace": s.workspace,
            "model": s.model,
            "created_at": s.created_at,
            "last_activity": s.last_activity,
            "messages": [{"role": m.role, "content": m.content} for m in s.conversation.messages],
        }

    @app.delete("/v1/sessions/{sid}")
    async def session_close(sid: str):
        ok = sm.close(sid)
        if not ok:
            raise HTTPException(status_code=404, detail="session not found")
        return {"closed": sid}

    @app.post("/v1/sessions/{sid}/messages")
    async def session_send_message(
        sid: str,
        payload: dict = Body(default_factory=dict),
    ):
        s = sm.get(sid)
        if s is None:
            raise HTTPException(status_code=404, detail="session not found")
        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            raise HTTPException(status_code=400, detail="content must be non-empty string")
        max_iters = int(payload.get("max_iterations", 25))
        async with s.lock:
            result = await _run_turn(s, content, max_iterations=max_iters)
            sm.touch(sid)
        return result

    @app.get("/v1/sessions/{sid}/events")
    async def session_stream(sid: str):
        """Server-Sent Events stream of agent events for this session."""
        s = sm.get(sid)
        if s is None:
            raise HTTPException(status_code=404, detail="session not found")

        async def event_gen() -> AsyncIterator[bytes]:
            # Heartbeat every 15s so idle clients don't disconnect behind
            # aggressive proxies. Data frames drop through as they arrive.
            while True:
                try:
                    event = await asyncio.wait_for(s.event_queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield b": heartbeat\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n".encode("utf-8")

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @app.websocket("/v1/ws/sessions/{sid}")
    async def session_ws(websocket: _FastAPIWebSocket, sid: str):  # type: ignore[valid-type]
        """Bidirectional WebSocket for a session — same event vocabulary
        as SSE, plus client-to-server control messages.

        Client → server JSON:
          ``{"type": "message", "content": "..."}``  — enqueue a turn
          ``{"type": "cancel"}``                      — cancel in-flight turn
          ``{"type": "ping"}``                        — keepalive

        Server → client JSON: same shape as SSE data frames
        (``text``/``tool_call``/``tool_result``/``error``/``done``).
        """
        from starlette.websockets import WebSocketDisconnect

        s = sm.get(sid)
        if s is None:
            await websocket.close(code=4404, reason="session not found")
            return
        await websocket.accept()

        async def send_events() -> None:
            """Drain the session's event queue into the socket."""
            try:
                while True:
                    event = await s.event_queue.get()
                    await websocket.send_json(event)
                    if event.get("kind") == "done":
                        # Done doesn't close the socket — a new message
                        # can start another turn on the same session.
                        continue
            except WebSocketDisconnect:
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("ws send_events ended: %s", exc)

        drain_task = asyncio.create_task(send_events())
        turn_task: asyncio.Task | None = None

        async def _locked_turn(content: str, max_iters: int) -> None:
            async with s.lock:
                await _run_turn(s, content, max_iterations=max_iters)

        try:
            while True:
                data = await websocket.receive_json()
                mtype = data.get("type")
                if mtype == "ping":
                    await websocket.send_json({"kind": "pong"})
                    continue
                if mtype == "cancel":
                    if turn_task and not turn_task.done():
                        turn_task.cancel()
                    await websocket.send_json({"kind": "cancelled"})
                    continue
                if mtype == "message":
                    content = (data.get("content") or "").strip()
                    if not content:
                        await websocket.send_json({"kind": "error", "text": "empty message"})
                        continue
                    max_iters = int(data.get("max_iterations") or 25)
                    if s.lock.locked():
                        await websocket.send_json({"kind": "error", "text": "session busy"})
                        continue
                    turn_task = asyncio.create_task(_locked_turn(content, max_iters))
                    sm.touch(sid)
                    continue
                await websocket.send_json({"kind": "error", "text": f"unknown type: {mtype!r}"})
        except WebSocketDisconnect:
            pass
        finally:
            drain_task.cancel()
            if turn_task and not turn_task.done():
                turn_task.cancel()
            for t in (drain_task, turn_task):
                if t is not None:
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

    return app


def serve(host: str = "127.0.0.1", port: int = 3030) -> None:
    """CLI entrypoint — launch uvicorn against create_app()."""
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("uvicorn not installed. Install the REST server extra: pip install 'karna[rest]'") from exc

    logging.basicConfig(
        level=logging.INFO,
        format="[nellie-rest] %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
