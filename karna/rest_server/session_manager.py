"""In-memory session manager for the REST server.

Each session holds a ``Conversation`` + per-session workspace + live
event queue. Sessions are identified by a UUID and live in-process
for the server's lifetime (there's no cross-restart persistence yet —
``karna/sessions/`` handles SQLite-backed history for the interactive
CLI, which uses a different persistence model).
"""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from karna.models import Conversation, Message


@dataclass
class Session:
    """One live agent session."""

    id: str
    workspace: str | None = None
    model: str | None = None
    conversation: Conversation = field(default_factory=Conversation)
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    # SSE subscribers read from this queue. Producer pushes dict events
    # from the agent loop; the streaming endpoint drains + emits them.
    event_queue: asyncio.Queue[dict[str, Any]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=1000)
    )
    # Guard concurrent messages on one session — the agent loop is not
    # re-entrant against a shared Conversation.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionManager:
    """Concurrent session store — one SessionManager per server process."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._create_lock = asyncio.Lock()

    async def create(
        self,
        *,
        workspace: str | None = None,
        model: str | None = None,
        system_instructions: str | None = None,
    ) -> Session:
        """Create a new session + seed optional system instructions."""
        async with self._create_lock:
            sid = secrets.token_urlsafe(12)
            while sid in self._sessions:
                sid = secrets.token_urlsafe(12)
            session = Session(id=sid, workspace=workspace, model=model)
            if workspace:
                os.makedirs(workspace, exist_ok=True)
            if system_instructions:
                # Stored as a ``system`` role message so build_system_prompt
                # (which composes a system prompt from tools/skills/project
                # context) can append to this rather than replace it.
                session.conversation.messages.append(
                    Message(role="system", content=system_instructions)
                )
            self._sessions[sid] = session
            return session

    def get(self, sid: str) -> Session | None:
        return self._sessions.get(sid)

    def list(self) -> list[Session]:
        return list(self._sessions.values())

    def close(self, sid: str) -> bool:
        return self._sessions.pop(sid, None) is not None

    def touch(self, sid: str) -> None:
        """Update last_activity for idle-eviction policies (future)."""
        s = self._sessions.get(sid)
        if s is not None:
            s.last_activity = time.time()
