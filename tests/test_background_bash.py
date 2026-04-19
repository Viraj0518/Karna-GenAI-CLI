"""Tests for background bash execution and task management.

Covers:
- BashTool run_in_background parameter
- Background process completion notification
- Background process timeout handling
- Timeout parameter clamping (max 600s)
- Integration with task registry
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from karna.tools.bash import BashTool, _MAX_TIMEOUT
from karna.tools.task_registry import (
    TaskStatus,
    TaskType,
    get_task_registry,
    reset_task_registry,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset the global task registry before each test."""
    reset_task_registry()
    yield
    reset_task_registry()


class TestBashBackgroundExecution:
    """Test the run_in_background parameter."""

    @pytest.mark.asyncio
    async def test_background_returns_immediately(self) -> None:
        tool = BashTool(safe_mode=False)
        result = await tool.execute(
            command="sleep 5 && echo done",
            run_in_background=True,
            description="slow command",
        )
        assert "Background task" in result
        assert "started" in result
        assert "bg_" in result
        assert "slow command" not in result or "Output file" in result

    @pytest.mark.asyncio
    async def test_background_creates_output_file(self) -> None:
        tool = BashTool(safe_mode=False)
        result = await tool.execute(
            command="echo hello_world",
            run_in_background=True,
            description="echo test",
        )
        # Extract output file path from result
        for line in result.splitlines():
            if "Output file:" in line:
                output_file = line.split("Output file:")[1].strip()
                break
        else:
            pytest.fail("No output file path in result")

        # Wait for the background process to complete
        await asyncio.sleep(1.0)

        assert os.path.exists(output_file)
        with open(output_file) as f:
            content = f.read()
        assert "hello_world" in content

        # Clean up
        os.unlink(output_file)

    @pytest.mark.asyncio
    async def test_background_registers_in_task_registry(self) -> None:
        tool = BashTool(safe_mode=False)
        await tool.execute(
            command="echo registered",
            run_in_background=True,
            description="registry test",
        )

        registry = get_task_registry()
        tasks = registry.list_all()
        bg_tasks = [t for t in tasks if t.type == TaskType.BASH]
        assert len(bg_tasks) == 1
        assert bg_tasks[0].description == "registry test"
        assert bg_tasks[0].status == TaskStatus.RUNNING

        # Wait for completion
        await asyncio.sleep(1.0)

        entry = registry.get(bg_tasks[0].id)
        assert entry is not None
        assert entry.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_background_completion_notification(self) -> None:
        tool = BashTool(safe_mode=False)
        await tool.execute(
            command="echo notify_me",
            run_in_background=True,
            description="notification test",
        )

        # Wait for the process to complete
        await asyncio.sleep(1.0)

        registry = get_task_registry()
        notifications = registry.get_pending_notifications()
        assert len(notifications) >= 1
        # Check format
        assert any("<task-notification>" in n for n in notifications)
        assert any("notify_me" in n for n in notifications)

    @pytest.mark.asyncio
    async def test_background_with_failing_command(self) -> None:
        tool = BashTool(safe_mode=False)
        await tool.execute(
            command="exit 42",
            run_in_background=True,
            description="failing command",
        )

        await asyncio.sleep(1.0)

        registry = get_task_registry()
        notifications = registry.get_pending_notifications()
        # Should still get a completion notification (with exit code)
        assert len(notifications) >= 1
        assert any("exit code 42" in n for n in notifications)

    @pytest.mark.asyncio
    async def test_sync_execution_unchanged(self) -> None:
        """Ensure synchronous (default) execution still works."""
        tool = BashTool(safe_mode=False)
        result = await tool.execute(command="echo sync_test")
        assert result.strip() == "sync_test"

    @pytest.mark.asyncio
    async def test_sync_execution_with_explicit_false(self) -> None:
        tool = BashTool(safe_mode=False)
        result = await tool.execute(
            command="echo explicit_false",
            run_in_background=False,
        )
        assert result.strip() == "explicit_false"


class TestBashTimeout:
    """Test timeout parameter handling."""

    @pytest.mark.asyncio
    async def test_timeout_clamped_to_max(self) -> None:
        tool = BashTool(safe_mode=False)
        # The execute method clamps timeout to _MAX_TIMEOUT
        result = await tool.execute(
            command="echo clamped",
            timeout=9999,
            run_in_background=True,
            description="timeout test",
        )
        # Should succeed — timeout is clamped, not rejected
        assert "Background task" in result
        assert "600s" in result  # clamped to max

        await asyncio.sleep(0.5)

    @pytest.mark.asyncio
    async def test_default_timeout(self) -> None:
        tool = BashTool(safe_mode=False)
        result = await tool.execute(
            command="echo default",
            run_in_background=True,
            description="default timeout",
        )
        assert "120s" in result

        await asyncio.sleep(0.5)

    @pytest.mark.asyncio
    async def test_background_timeout_produces_error(self) -> None:
        tool = BashTool(safe_mode=False)
        await tool.execute(
            command="sleep 100",
            timeout=1,
            run_in_background=True,
            description="timeout test",
        )

        await asyncio.sleep(2.5)

        registry = get_task_registry()
        bg_tasks = [t for t in registry.list_all() if t.type == TaskType.BASH]
        assert len(bg_tasks) == 1
        assert bg_tasks[0].status == TaskStatus.FAILED


class TestBashSafeMode:
    """Ensure safe_mode still works with new parameters."""

    @pytest.mark.asyncio
    async def test_safe_mode_blocks_in_background(self) -> None:
        tool = BashTool(safe_mode=True)
        result = await tool.execute(
            command="rm -rf /",
            run_in_background=True,
            description="dangerous",
        )
        # Should be blocked before background execution
        assert "BLOCKED" in result

    @pytest.mark.asyncio
    async def test_safe_mode_blocks_sync(self) -> None:
        tool = BashTool(safe_mode=True)
        result = await tool.execute(command="rm -rf /")
        assert "BLOCKED" in result


class TestBashToolSchema:
    """Verify the tool schema includes new parameters."""

    def test_schema_has_run_in_background(self) -> None:
        tool = BashTool()
        props = tool.parameters["properties"]
        assert "run_in_background" in props
        assert props["run_in_background"]["type"] == "boolean"

    def test_schema_has_description(self) -> None:
        tool = BashTool()
        props = tool.parameters["properties"]
        assert "description" in props

    def test_schema_has_timeout(self) -> None:
        tool = BashTool()
        props = tool.parameters["properties"]
        assert "timeout" in props
