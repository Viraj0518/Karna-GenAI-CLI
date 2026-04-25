"""Unified task registry for all background work.

Tracks monitors, background bash commands, and subagents in a single
registry.  Provides methods to register, unregister, list, stop tasks
and drain pending notification events.

Each task entry carries an async-safe event queue so that the agent
loop can inject completion/output notifications between turns.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class TaskType(str, Enum):
    MONITOR = "monitor"
    BASH = "bash"
    SUBAGENT = "subagent"


class TaskStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskEntry:
    """A single tracked background task."""

    id: str
    type: TaskType
    description: str
    status: TaskStatus = TaskStatus.RUNNING
    started_at: float = field(default_factory=time.time)
    events: list[str] = field(default_factory=list)
    _asyncio_task: asyncio.Task[Any] | None = field(default=None, repr=False)

    @property
    def runtime_seconds(self) -> float:
        return time.time() - self.started_at

    @property
    def runtime_display(self) -> str:
        secs = self.runtime_seconds
        if secs < 60:
            return f"{secs:.0f}s"
        mins = int(secs // 60)
        remaining = int(secs % 60)
        return f"{mins}m{remaining}s"


class TaskRegistry:
    """Central registry for monitors, background bash, and subagents.

    Thread-safe via the GIL for dict mutations; event queues use
    ``asyncio.Queue`` for async notification draining.

    Singleton pattern — use ``get_task_registry()`` to access.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskEntry] = {}
        self._pending_notifications: asyncio.Queue[str] = asyncio.Queue()
        # Batch window for event coalescing (seconds)
        self._batch_window: float = 0.2

    def register(
        self,
        task_id: str,
        task_type: TaskType,
        description: str,
        asyncio_task: asyncio.Task[Any] | None = None,
    ) -> TaskEntry:
        """Register a new background task."""
        entry = TaskEntry(
            id=task_id,
            type=task_type,
            description=description,
            _asyncio_task=asyncio_task,
        )
        self._tasks[task_id] = entry
        logger.info("Task registered: %s (%s) — %s", task_id, task_type.value, description)
        return entry

    def unregister(self, task_id: str) -> bool:
        """Remove a task from the registry. Returns True if it existed."""
        return self._tasks.pop(task_id, None) is not None

    def get(self, task_id: str) -> TaskEntry | None:
        """Look up a task by ID."""
        return self._tasks.get(task_id)

    def list_all(self) -> list[TaskEntry]:
        """Return all tasks (active and completed)."""
        return list(self._tasks.values())

    def list_active(self) -> list[TaskEntry]:
        """Return only tasks that are still running."""
        return [t for t in self._tasks.values() if t.status == TaskStatus.RUNNING]

    def add_event(self, task_id: str, event_text: str) -> None:
        """Append an event to a task and queue a notification."""
        entry = self._tasks.get(task_id)
        if entry is None:
            logger.warning("add_event called for unknown task %s", task_id)
            return
        entry.events.append(event_text)

    def queue_notification(self, notification: str) -> None:
        """Queue a formatted notification for injection into the conversation."""
        self._pending_notifications.put_nowait(notification)

    def complete_task(self, task_id: str, final_event: str | None = None) -> None:
        """Mark a task as completed and optionally queue a final notification."""
        entry = self._tasks.get(task_id)
        if entry is None:
            return
        entry.status = TaskStatus.COMPLETED
        if final_event:
            entry.events.append(final_event)
            notification = format_task_notification(
                task_id=task_id,
                description=entry.description,
                event_text=final_event,
            )
            self.queue_notification(notification)
        logger.info("Task completed: %s", task_id)

    def fail_task(self, task_id: str, error: str) -> None:
        """Mark a task as failed."""
        entry = self._tasks.get(task_id)
        if entry is None:
            return
        entry.status = TaskStatus.FAILED
        entry.events.append(f"[error] {error}")
        notification = format_task_notification(
            task_id=task_id,
            description=entry.description,
            event_text=f"[error] {error}",
        )
        self.queue_notification(notification)
        logger.info("Task failed: %s — %s", task_id, error)

    async def stop(self, task_id: str) -> bool:
        """Cancel a running task. Returns True if cancelled."""
        entry = self._tasks.get(task_id)
        if entry is None:
            return False
        if entry.status != TaskStatus.RUNNING:
            return False
        if entry._asyncio_task is not None and not entry._asyncio_task.done():
            entry._asyncio_task.cancel()
            try:
                await entry._asyncio_task
            except asyncio.CancelledError:
                pass
        entry.status = TaskStatus.CANCELLED
        entry.events.append("[cancelled]")
        logger.info("Task stopped: %s", task_id)
        return True

    async def shutdown(self) -> None:
        """Cancel every running task and await its teardown.

        Required before tearing down the asyncio event loop — otherwise orphan
        background ``asyncio.Task``s keep references to the loop's subprocess
        transports, which on the Windows Proactor loop blocks the next event
        loop from reaping subprocess pipes. Symptom: the NEXT test that spawns
        a subprocess hangs forever.
        """
        running = [
            entry
            for entry in self._tasks.values()
            if entry.status == TaskStatus.RUNNING and entry._asyncio_task is not None and not entry._asyncio_task.done()
        ]
        for entry in running:
            assert entry._asyncio_task is not None
            entry._asyncio_task.cancel()
        for entry in running:
            assert entry._asyncio_task is not None
            try:
                await entry._asyncio_task
            except (asyncio.CancelledError, Exception):
                pass
            entry.status = TaskStatus.CANCELLED

    def get_pending_notifications(self) -> list[str]:
        """Drain all pending notifications (non-blocking).

        Returns a list of formatted notification strings ready for
        injection into the conversation as system messages.
        """
        notifications: list[str] = []
        while True:
            try:
                n = self._pending_notifications.get_nowait()
                notifications.append(n)
            except asyncio.QueueEmpty:
                break
        return notifications

    def has_pending_notifications(self) -> bool:
        """Check if there are pending notifications without draining."""
        return not self._pending_notifications.empty()

    def clear_completed(self) -> int:
        """Remove all completed/failed/cancelled tasks. Returns count removed."""
        to_remove = [tid for tid, t in self._tasks.items() if t.status != TaskStatus.RUNNING]
        for tid in to_remove:
            del self._tasks[tid]
        return len(to_remove)


# --------------------------------------------------------------------------- #
#  Notification formatting
# --------------------------------------------------------------------------- #


def format_task_notification(
    task_id: str,
    description: str,
    event_text: str,
) -> str:
    """Format a task event as an XML notification block.

    This format is injected into the conversation as a system message
    so the LLM can see background task updates.
    """
    return (
        f"<task-notification>\n"
        f"<task-id>{task_id}</task-id>\n"
        f'<summary>Monitor event: "{description}"</summary>\n'
        f"<event>{event_text}</event>\n"
        f"</task-notification>"
    )


# --------------------------------------------------------------------------- #
#  Singleton access
# --------------------------------------------------------------------------- #

_registry: TaskRegistry | None = None


def get_task_registry() -> TaskRegistry:
    """Return the global TaskRegistry singleton."""
    global _registry
    if _registry is None:
        _registry = TaskRegistry()
    return _registry


def reset_task_registry() -> None:
    """Reset the global registry (for testing)."""
    global _registry
    _registry = TaskRegistry()
