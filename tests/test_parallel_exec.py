"""Tests for karna.agents.parallel.execute_tools_parallel."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from karna.agents.parallel import execute_tools_parallel
from karna.models import ToolCall
from karna.tools.base import BaseTool


class _SleepTool(BaseTool):
    """Tool that sleeps for ``delay`` seconds then returns its name."""

    def __init__(self, name: str, delay: float, sequential: bool = False) -> None:
        self.name = name
        self.description = f"sleep {delay}"
        self.parameters = {"type": "object", "properties": {}}
        self.sequential = sequential
        self._delay = delay

    async def execute(self, **kwargs: Any) -> str:
        await asyncio.sleep(self._delay)
        return f"done:{self.name}"


class _BoomTool(BaseTool):
    name = "boom"
    description = "raises"
    parameters = {"type": "object", "properties": {}}
    sequential = False

    async def execute(self, **kwargs: Any) -> str:
        raise RuntimeError("boom")


def _tc(name: str, call_id: str = "") -> ToolCall:
    return ToolCall(id=call_id or f"c-{name}", name=name, arguments={})


@pytest.mark.asyncio
async def test_independent_tools_run_concurrently() -> None:
    """Three 0.2s parallel tools should finish in well under 0.6s."""
    registry = {
        "read_a": _SleepTool("read_a", 0.2),
        "read_b": _SleepTool("read_b", 0.2),
        "read_c": _SleepTool("read_c", 0.2),
    }
    calls = [_tc("read_a"), _tc("read_b"), _tc("read_c")]

    t0 = time.perf_counter()
    results = await execute_tools_parallel(calls, registry)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.5, f"concurrent execution took {elapsed:.3f}s (expected < 0.5s)"
    assert [r.content for r in results] == ["done:read_a", "done:read_b", "done:read_c"]
    assert not any(r.is_error for r in results)


@pytest.mark.asyncio
async def test_sequential_tools_are_serialised() -> None:
    """Two 0.2s sequential tools should take at least ~0.4s combined."""
    registry = {
        "bash_a": _SleepTool("bash_a", 0.2, sequential=True),
        "bash_b": _SleepTool("bash_b", 0.2, sequential=True),
    }
    calls = [_tc("bash_a"), _tc("bash_b")]

    t0 = time.perf_counter()
    results = await execute_tools_parallel(calls, registry)
    elapsed = time.perf_counter() - t0

    assert elapsed >= 0.35, f"sequential tools ran in {elapsed:.3f}s (too fast)"
    assert [r.content for r in results] == ["done:bash_a", "done:bash_b"]


@pytest.mark.asyncio
async def test_permission_rejection_does_not_crash_siblings() -> None:
    registry = {
        "read_a": _SleepTool("read_a", 0.05),
        "bash_x": _SleepTool("bash_x", 0.05, sequential=True),
    }
    calls = [_tc("read_a"), _tc("bash_x")]

    def deny_bash(tc: ToolCall) -> bool:
        return tc.name != "bash_x"

    results = await execute_tools_parallel(calls, registry, permission_check=deny_bash)

    assert results[0].content == "done:read_a"
    assert results[0].is_error is False
    assert results[1].is_error is True
    assert "Permission denied" in results[1].content


@pytest.mark.asyncio
async def test_order_preserved_with_mixed_delays() -> None:
    """Results must appear in the original call order regardless of finish order."""
    registry = {
        "slow": _SleepTool("slow", 0.2),
        "fast": _SleepTool("fast", 0.01),
    }
    calls = [_tc("slow", "id-0"), _tc("fast", "id-1")]
    results = await execute_tools_parallel(calls, registry)

    assert results[0].tool_call_id == "id-0"
    assert results[0].content == "done:slow"
    assert results[1].tool_call_id == "id-1"
    assert results[1].content == "done:fast"


@pytest.mark.asyncio
async def test_exception_in_one_tool_is_isolated() -> None:
    registry = {"boom": _BoomTool(), "ok": _SleepTool("ok", 0.01)}
    calls = [_tc("boom"), _tc("ok")]
    results = await execute_tools_parallel(calls, registry)
    assert results[0].is_error is True
    assert "boom" in results[0].content
    assert results[1].content == "done:ok"
    assert results[1].is_error is False


@pytest.mark.asyncio
async def test_unknown_tool_produces_error_result() -> None:
    results = await execute_tools_parallel([_tc("nope")], {})
    assert results[0].is_error is True
    assert "Unknown tool" in results[0].content
