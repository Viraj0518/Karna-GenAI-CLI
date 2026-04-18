"""Parallel tool execution helper.

Runs independent tool calls concurrently via ``asyncio.gather`` while
preserving a strict sequential lane for tools marked
``sequential = True`` (bash, write, edit, etc.). When the model emits
three parallel-safe tool calls (e.g. read three files), running them
concurrently cuts turn latency by roughly Nx rather than summing the
per-call wall time.

# TODO(wiring): import this from agents/loop.py in a follow-up PR
#               — this module intentionally does not touch loop.py so
#               multiple agents can land their changes independently.

The helper mirrors the partitioning logic that already exists in
``karna.agents.loop._execute_tool_calls`` but with:

* An injected ``permission_check`` callable instead of a
  ``PermissionManager`` (so callers outside the main loop — subagents,
  plan-mode, autonomous — can reuse it).
* A ``max_concurrent`` semaphore so a model that fans out 20 reads at
  once doesn't blow the open-file-descriptor limit.
* Deterministic result ordering: outputs are always returned in the
  original ``tool_calls`` order regardless of scheduling.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from karna.models import ToolCall, ToolResult
from karna.tools.base import BaseTool

logger = logging.getLogger(__name__)

_DEFAULT_TOOL_TIMEOUT = 120.0


PermissionCheck = Callable[[ToolCall], bool]


async def _run_one(
    tool_call: ToolCall,
    tool: BaseTool,
    *,
    timeout: float,
) -> ToolResult:
    """Execute a single tool call and package the result.

    Catches every exception so that a sibling failure in an
    ``asyncio.gather`` never cancels the rest of the batch.
    """
    try:
        content = await asyncio.wait_for(tool.execute(**tool_call.arguments), timeout=timeout)
        return ToolResult(tool_call_id=tool_call.id, content=content, is_error=False)
    except asyncio.TimeoutError:
        msg = f"Tool '{tool.name}' timed out after {timeout:.0f}s"
        logger.warning(msg)
        return ToolResult(tool_call_id=tool_call.id, content=msg, is_error=True)
    except Exception as exc:  # noqa: BLE001 — we really do want to swallow everything
        msg = f"Tool '{tool.name}' failed: {type(exc).__name__}: {exc}"
        logger.exception("Tool %s raised: %s", tool.name, exc)
        return ToolResult(tool_call_id=tool_call.id, content=msg, is_error=True)


async def execute_tools_parallel(
    tool_calls: list[ToolCall],
    tool_registry: dict[str, BaseTool],
    *,
    permission_check: PermissionCheck | None = None,
    max_concurrent: int = 5,
    timeout: float = _DEFAULT_TOOL_TIMEOUT,
) -> list[ToolResult]:
    """Execute ``tool_calls`` concurrently where safe.

    Rules:
    - Tools with ``BaseTool.sequential = True`` run strictly one at a
      time, in original order. Any assumption about mutable shared
      state (cwd, filesystem, stdout) holds.
    - All other tools run under an ``asyncio.Semaphore(max_concurrent)``
      so we get concurrency without unbounded fan-out.
    - ``permission_check`` is called once per tool call *before* any
      execution. A ``False`` return produces an error ``ToolResult`` and
      skips the tool entirely — it never crashes siblings.
    - Results are returned in the original ``tool_calls`` order.
    """
    if not tool_calls:
        return []

    # Phase 1: resolve + permission-check every call up front. Each
    # slot in ``results`` is either filled now (immediate error) or
    # left None and filled by the scheduler below.
    results: list[ToolResult | None] = [None] * len(tool_calls)
    parallel_indices: list[int] = []
    sequential_indices: list[int] = []

    for idx, tc in enumerate(tool_calls):
        if permission_check is not None and not permission_check(tc):
            results[idx] = ToolResult(
                tool_call_id=tc.id,
                content=f"Permission denied for tool '{tc.name}'.",
                is_error=True,
            )
            continue

        tool = tool_registry.get(tc.name)
        if tool is None:
            results[idx] = ToolResult(
                tool_call_id=tc.id,
                content=f"[error] Unknown tool: {tc.name}",
                is_error=True,
            )
            continue

        if tool.sequential:
            sequential_indices.append(idx)
        else:
            parallel_indices.append(idx)

    # Phase 2: parallel fan-out.
    if parallel_indices:
        sem = asyncio.Semaphore(max(1, max_concurrent))

        async def _bounded(idx: int) -> tuple[int, ToolResult]:
            tc = tool_calls[idx]
            tool = tool_registry[tc.name]
            async with sem:
                result = await _run_one(tc, tool, timeout=timeout)
            return idx, result

        gathered = await asyncio.gather(*[_bounded(i) for i in parallel_indices])
        for idx, result in gathered:
            results[idx] = result

    # Phase 3: sequential lane — one at a time, preserving order.
    for idx in sequential_indices:
        tc = tool_calls[idx]
        tool = tool_registry[tc.name]
        results[idx] = await _run_one(tc, tool, timeout=timeout)

    # Every slot must be filled by now.
    final: list[ToolResult] = []
    for idx, res in enumerate(results):
        if res is None:  # pragma: no cover — defensive
            final.append(
                ToolResult(
                    tool_call_id=tool_calls[idx].id,
                    content="[internal] Tool result was never produced.",
                    is_error=True,
                )
            )
        else:
            final.append(res)
    return final


__all__ = ["execute_tools_parallel", "PermissionCheck"]
