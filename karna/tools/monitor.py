"""Monitor tool — stream events from a background process.

Each stdout line from the monitored process becomes a notification
event that can be surfaced to the TUI. The tool returns immediately
with a monitor ID; events are collected asynchronously.

Integrates with the unified :class:`~karna.tools.task_registry.TaskRegistry`
so that monitor events are surfaced as notifications into the active
conversation between agent turns.

Ported from cc-src LocalShellSpawnTask / monitor patterns with
attribution to the Anthropic Claude Code codebase.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable
from uuid import uuid4

from karna.tools.base import BaseTool
from karna.tools.task_registry import (
    TaskType,
    format_task_notification,
    get_task_registry,
)

logger = logging.getLogger(__name__)


class MonitorTool(BaseTool):
    """Stream events from a background process.

    Each stdout line becomes a notification. The tool returns
    immediately with a monitor ID. Events are collected
    asynchronously and can be retrieved later.

    When ``persistent=True``, the monitor has no timeout and
    survives across agent turns until explicitly cancelled.
    """

    name = "monitor"
    description = (
        "Stream events from a background process. Each stdout line becomes "
        "a notification. Returns immediately with a monitor ID."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to monitor.",
            },
            "description": {
                "type": "string",
                "description": "What you're monitoring (for display).",
            },
            "timeout": {
                "type": "integer",
                "default": 300,
                "description": "Timeout in seconds (default 300). Ignored when persistent=True.",
            },
            "persistent": {
                "type": "boolean",
                "default": False,
                "description": (
                    "When true, the monitor has no timeout and survives across turns until explicitly cancelled."
                ),
            },
        },
        "required": ["command", "description"],
    }

    def __init__(self) -> None:
        super().__init__()
        self._active_monitors: dict[str, asyncio.Task[None]] = {}
        self._events: dict[str, list[str]] = {}
        self._descriptions: dict[str, str] = {}
        self._on_event: Callable[[str, str], None] | None = None

    # ------------------------------------------------------------------ #
    #  Public helpers
    # ------------------------------------------------------------------ #

    def set_event_handler(self, handler: Callable[[str, str], None]) -> None:
        """Register a callback ``(monitor_id, line)`` for real-time events.

        The TUI layer can hook into this to display live output.
        """
        self._on_event = handler

    def get_events(self, monitor_id: str) -> list[str]:
        """Return all events collected so far for *monitor_id*."""
        return list(self._events.get(monitor_id, []))

    def is_active(self, monitor_id: str) -> bool:
        """Check whether a monitor is still running."""
        task = self._active_monitors.get(monitor_id)
        return task is not None and not task.done()

    def list_monitors(self) -> dict[str, bool]:
        """Return ``{monitor_id: is_active}`` for all monitors."""
        return {mid: not task.done() for mid, task in self._active_monitors.items()}

    async def cancel(self, monitor_id: str) -> bool:
        """Cancel a running monitor. Returns True if cancelled."""
        task = self._active_monitors.get(monitor_id)
        if task is None or task.done():
            return False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._emit_event(monitor_id, f"[Monitor {monitor_id} cancelled]")
        # Update task registry
        registry = get_task_registry()
        await registry.stop(monitor_id)
        return True

    # ------------------------------------------------------------------ #
    #  Core execute
    # ------------------------------------------------------------------ #

    async def execute(self, **kwargs: Any) -> str:
        command: str = kwargs["command"]
        description: str = kwargs["description"]
        timeout: int = kwargs.get("timeout", 300)
        persistent: bool = kwargs.get("persistent", False)

        monitor_id = f"mon_{uuid4().hex[:8]}"
        self._events[monitor_id] = []
        self._descriptions[monitor_id] = description

        # Persistent monitors have no timeout
        effective_timeout = 0 if persistent else timeout

        task = asyncio.create_task(
            self._stream_process(command, monitor_id, effective_timeout, description),
            name=f"monitor-{monitor_id}",
        )
        self._active_monitors[monitor_id] = task

        # Register with the unified task registry
        registry = get_task_registry()
        registry.register(
            task_id=monitor_id,
            task_type=TaskType.MONITOR,
            description=description,
            asyncio_task=task,
        )

        logger.info(
            "Started monitor %s: %s (timeout=%s, persistent=%s)",
            monitor_id,
            description,
            "none" if persistent else f"{timeout}s",
            persistent,
        )

        timeout_info = "persistent (no timeout)" if persistent else f"{timeout}s"
        return (
            f"Monitor {monitor_id} started: {description}\n"
            f"Command: {command}\n"
            f"Timeout: {timeout_info}\n"
            f"Events will be collected in the background."
        )

    # ------------------------------------------------------------------ #
    #  Internal streaming
    # ------------------------------------------------------------------ #

    async def _stream_process(
        self,
        command: str,
        monitor_id: str,
        timeout: int,
        description: str,
    ) -> None:
        """Run *command* and capture each stdout line as an event.

        When *timeout* is 0, the process runs without a timeout
        (persistent monitor mode).
        """
        proc: asyncio.subprocess.Process | None = None
        registry = get_task_registry()
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            assert proc.stdout is not None  # guaranteed by PIPE

            try:
                if timeout > 0:
                    async with asyncio.timeout(timeout):
                        async for line in proc.stdout:
                            decoded = line.decode("utf-8", errors="replace").rstrip()
                            self._emit_event(monitor_id, decoded)
                else:
                    # Persistent mode — no timeout
                    async for line in proc.stdout:
                        decoded = line.decode("utf-8", errors="replace").rstrip()
                        self._emit_event(monitor_id, decoded)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                msg = f"[Monitor {monitor_id} timed out after {timeout}s]"
                self._emit_event(monitor_id, msg)
                registry.complete_task(monitor_id, msg)
                return

            await proc.wait()
            msg = f"[Monitor {monitor_id} completed with exit code {proc.returncode}]"
            self._emit_event(monitor_id, msg)
            registry.complete_task(monitor_id, msg)

        except asyncio.CancelledError:
            if proc is not None:
                proc.kill()
                await proc.wait()
            raise
        except Exception as exc:
            msg = f"[Monitor {monitor_id} error: {exc}]"
            self._emit_event(monitor_id, msg)
            registry.fail_task(monitor_id, str(exc))

    def _emit_event(self, monitor_id: str, line: str) -> None:
        """Record an event and notify any registered handler."""
        events = self._events.setdefault(monitor_id, [])
        events.append(line)
        logger.debug("Monitor %s: %s", monitor_id, line)

        # Push to task registry for conversation injection
        registry = get_task_registry()
        registry.add_event(monitor_id, line)
        description = self._descriptions.get(monitor_id, "background process")
        notification = format_task_notification(
            task_id=monitor_id,
            description=description,
            event_text=line,
        )
        registry.queue_notification(notification)

        if self._on_event is not None:
            try:
                self._on_event(monitor_id, line)
            except Exception:
                logger.warning("Event handler raised for monitor %s", monitor_id, exc_info=True)
