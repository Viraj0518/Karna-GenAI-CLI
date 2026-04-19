"""Tests for monitor event injection, persistent monitors, and task registry.

Covers:
- Task registry CRUD (register, list, stop, complete, fail)
- Task notification formatting
- Monitor tool integration with the task registry
- Persistent vs timeout monitors
- Event notification injection into the agent loop
- /tasks slash command output
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from karna.tools.task_registry import (
    TaskEntry,
    TaskRegistry,
    TaskStatus,
    TaskType,
    format_task_notification,
    get_task_registry,
    reset_task_registry,
)


# --------------------------------------------------------------------------- #
#  Task registry CRUD
# --------------------------------------------------------------------------- #


class TestTaskRegistry:
    """Unit tests for the TaskRegistry class."""

    def setup_method(self) -> None:
        self.registry = TaskRegistry()

    def test_register_creates_entry(self) -> None:
        entry = self.registry.register("t1", TaskType.MONITOR, "test monitor")
        assert entry.id == "t1"
        assert entry.type == TaskType.MONITOR
        assert entry.description == "test monitor"
        assert entry.status == TaskStatus.RUNNING

    def test_get_returns_registered_entry(self) -> None:
        self.registry.register("t1", TaskType.BASH, "test bash")
        entry = self.registry.get("t1")
        assert entry is not None
        assert entry.type == TaskType.BASH

    def test_get_returns_none_for_unknown(self) -> None:
        assert self.registry.get("nonexistent") is None

    def test_list_all_returns_everything(self) -> None:
        self.registry.register("t1", TaskType.MONITOR, "mon")
        self.registry.register("t2", TaskType.BASH, "bash")
        assert len(self.registry.list_all()) == 2

    def test_list_active_filters_completed(self) -> None:
        self.registry.register("t1", TaskType.MONITOR, "mon")
        self.registry.register("t2", TaskType.BASH, "bash")
        self.registry.complete_task("t1", "done")
        active = self.registry.list_active()
        assert len(active) == 1
        assert active[0].id == "t2"

    def test_unregister_removes_entry(self) -> None:
        self.registry.register("t1", TaskType.MONITOR, "mon")
        assert self.registry.unregister("t1")
        assert self.registry.get("t1") is None
        assert not self.registry.unregister("t1")  # second call returns False

    def test_complete_task_sets_status_and_queues_notification(self) -> None:
        self.registry.register("t1", TaskType.BASH, "my bash")
        self.registry.complete_task("t1", "finished ok")
        entry = self.registry.get("t1")
        assert entry is not None
        assert entry.status == TaskStatus.COMPLETED
        assert "finished ok" in entry.events
        # Notification should be queued
        notifications = self.registry.get_pending_notifications()
        assert len(notifications) == 1
        assert "<task-id>t1</task-id>" in notifications[0]

    def test_fail_task_sets_status(self) -> None:
        self.registry.register("t1", TaskType.MONITOR, "mon")
        self.registry.fail_task("t1", "timeout")
        entry = self.registry.get("t1")
        assert entry is not None
        assert entry.status == TaskStatus.FAILED

    def test_add_event_appends_to_task(self) -> None:
        self.registry.register("t1", TaskType.MONITOR, "mon")
        self.registry.add_event("t1", "line 1")
        self.registry.add_event("t1", "line 2")
        entry = self.registry.get("t1")
        assert entry is not None
        assert entry.events == ["line 1", "line 2"]

    def test_add_event_unknown_task_is_noop(self) -> None:
        # Should not raise
        self.registry.add_event("nonexistent", "data")

    @pytest.mark.asyncio
    async def test_stop_cancels_running_task(self) -> None:
        async def _long_running() -> None:
            await asyncio.sleep(100)

        atask = asyncio.create_task(_long_running())
        self.registry.register("t1", TaskType.BASH, "bg", asyncio_task=atask)
        stopped = await self.registry.stop("t1")
        assert stopped
        entry = self.registry.get("t1")
        assert entry is not None
        assert entry.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_stop_returns_false_for_completed(self) -> None:
        self.registry.register("t1", TaskType.BASH, "bg")
        self.registry.complete_task("t1")
        stopped = await self.registry.stop("t1")
        assert not stopped

    def test_has_pending_and_drain(self) -> None:
        assert not self.registry.has_pending_notifications()
        self.registry.queue_notification("hello")
        assert self.registry.has_pending_notifications()
        msgs = self.registry.get_pending_notifications()
        assert msgs == ["hello"]
        assert not self.registry.has_pending_notifications()

    def test_clear_completed(self) -> None:
        self.registry.register("t1", TaskType.MONITOR, "mon")
        self.registry.register("t2", TaskType.BASH, "bash")
        self.registry.complete_task("t1")
        removed = self.registry.clear_completed()
        assert removed == 1
        assert self.registry.get("t1") is None
        assert self.registry.get("t2") is not None

    def test_runtime_display(self) -> None:
        import time

        entry = TaskEntry(id="t1", type=TaskType.BASH, description="test")
        entry.started_at = time.time() - 65  # 1m 5s
        display = entry.runtime_display
        assert display == "1m5s"

    def test_runtime_display_seconds(self) -> None:
        import time

        entry = TaskEntry(id="t1", type=TaskType.BASH, description="test")
        entry.started_at = time.time() - 42
        assert entry.runtime_display == "42s"


# --------------------------------------------------------------------------- #
#  Notification formatting
# --------------------------------------------------------------------------- #


class TestNotificationFormat:
    """Test the XML notification format."""

    def test_format_basic(self) -> None:
        result = format_task_notification(
            task_id="mon_abc123",
            description="watching build",
            event_text="Build succeeded",
        )
        assert "<task-notification>" in result
        assert "<task-id>mon_abc123</task-id>" in result
        assert 'Monitor event: "watching build"' in result
        assert "<event>Build succeeded</event>" in result
        assert "</task-notification>" in result

    def test_format_with_special_chars(self) -> None:
        result = format_task_notification(
            task_id="t1",
            description='test "quotes"',
            event_text="line with <xml> & entities",
        )
        assert "<event>line with <xml> & entities</event>" in result


# --------------------------------------------------------------------------- #
#  Singleton access
# --------------------------------------------------------------------------- #


class TestSingleton:
    def test_get_returns_same_instance(self) -> None:
        reset_task_registry()
        r1 = get_task_registry()
        r2 = get_task_registry()
        assert r1 is r2

    def test_reset_creates_new_instance(self) -> None:
        reset_task_registry()
        r1 = get_task_registry()
        reset_task_registry()
        r2 = get_task_registry()
        assert r1 is not r2


# --------------------------------------------------------------------------- #
#  Monitor tool with registry integration
# --------------------------------------------------------------------------- #


class TestMonitorToolIntegration:
    """Test MonitorTool integration with the task registry."""

    def setup_method(self) -> None:
        reset_task_registry()

    @pytest.mark.asyncio
    async def test_monitor_registers_in_task_registry(self) -> None:
        from karna.tools.monitor import MonitorTool

        tool = MonitorTool()
        result = await tool.execute(
            command="echo hello",
            description="test echo",
            timeout=10,
        )
        assert "started" in result.lower()

        registry = get_task_registry()
        tasks = registry.list_all()
        assert len(tasks) >= 1
        monitor_tasks = [t for t in tasks if t.type == TaskType.MONITOR]
        assert len(monitor_tasks) == 1
        assert monitor_tasks[0].description == "test echo"

        # Wait for the process to complete
        await asyncio.sleep(0.5)

    @pytest.mark.asyncio
    async def test_persistent_monitor_no_timeout_in_output(self) -> None:
        from karna.tools.monitor import MonitorTool

        tool = MonitorTool()
        result = await tool.execute(
            command="echo persistent",
            description="persistent test",
            persistent=True,
        )
        assert "persistent (no timeout)" in result

        # Wait for it to finish
        await asyncio.sleep(0.5)

    @pytest.mark.asyncio
    async def test_monitor_events_queued_as_notifications(self) -> None:
        from karna.tools.monitor import MonitorTool

        tool = MonitorTool()
        await tool.execute(
            command="echo line1 && echo line2",
            description="multi-line test",
            timeout=10,
        )

        # Wait for process to emit events
        await asyncio.sleep(1.0)

        registry = get_task_registry()
        notifications = registry.get_pending_notifications()
        # Should have notifications for each line + completion
        assert len(notifications) >= 2
        # Check format
        assert any("<task-notification>" in n for n in notifications)

    @pytest.mark.asyncio
    async def test_monitor_cancel_updates_registry(self) -> None:
        from karna.tools.monitor import MonitorTool

        tool = MonitorTool()
        result = await tool.execute(
            command="sleep 100",
            description="long sleep",
            timeout=300,
        )
        # Extract monitor ID from result
        monitor_id = result.split("started:")[0].split()[-1]

        cancelled = await tool.cancel(monitor_id)
        assert cancelled

        registry = get_task_registry()
        entry = registry.get(monitor_id)
        assert entry is not None
        assert entry.status == TaskStatus.CANCELLED


# --------------------------------------------------------------------------- #
#  /tasks slash command
# --------------------------------------------------------------------------- #


class TestTasksSlashCommand:
    """Test the /tasks slash command."""

    def setup_method(self) -> None:
        reset_task_registry()

    def test_tasks_empty(self) -> None:
        from karna.tui.slash import _cmd_tasks

        console = MagicMock()
        result = _cmd_tasks(console=console, args="")
        assert result is None
        console.print.assert_called_once()
        call_args = str(console.print.call_args)
        assert "No background tasks" in call_args

    def test_tasks_lists_registered(self) -> None:
        from karna.tui.slash import _cmd_tasks

        registry = get_task_registry()
        registry.register("t1", TaskType.MONITOR, "watching build")
        registry.register("t2", TaskType.BASH, "running tests")

        console = MagicMock()
        result = _cmd_tasks(console=console, args="")
        assert result is None
        # Console.print was called with a Table
        console.print.assert_called_once()

    def test_tasks_stop_unknown(self) -> None:
        from karna.tui.slash import _cmd_tasks

        console = MagicMock()
        result = _cmd_tasks(console=console, args="stop nonexistent")
        assert result is None
        call_args = str(console.print.call_args)
        assert "Unknown task" in call_args

    def test_tasks_stop_no_id(self) -> None:
        from karna.tui.slash import _cmd_tasks

        console = MagicMock()
        result = _cmd_tasks(console=console, args="stop")
        assert result is None
        call_args = str(console.print.call_args)
        assert "Usage" in call_args

    def test_tasks_stop_completed_task(self) -> None:
        from karna.tui.slash import _cmd_tasks

        registry = get_task_registry()
        registry.register("t1", TaskType.BASH, "done task")
        registry.complete_task("t1")

        console = MagicMock()
        result = _cmd_tasks(console=console, args="stop t1")
        assert result is None
        call_args = str(console.print.call_args)
        assert "not running" in call_args
