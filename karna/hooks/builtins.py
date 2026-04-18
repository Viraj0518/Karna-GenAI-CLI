"""Built-in hooks for common session-level concerns.

These are registered automatically by the hook dispatcher unless
disabled in config.  They cover:

- Cost warning when session spend exceeds a threshold
- Git dirty-tree warning on session start
- Auto-save memory after assistant responses (stub)

Adapted from cc-src hook patterns.  See NOTICES.md for attribution.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

from karna.hooks.dispatcher import HookResult

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------- #
#  Cost warning
# ----------------------------------------------------------------------- #

# Session-level accumulator — set by the agent loop / usage tracker.
_session_cost_usd: float = 0.0
_COST_THRESHOLD_USD: float = 1.0


def set_session_cost(cost: float) -> None:
    """Update the running session cost (called by the usage tracker)."""
    global _session_cost_usd
    _session_cost_usd = cost


def set_cost_threshold(threshold: float) -> None:
    """Override the default cost warning threshold."""
    global _COST_THRESHOLD_USD
    _COST_THRESHOLD_USD = threshold


async def cost_warning_hook(tool_name: str = "", **kwargs: Any) -> HookResult:
    """Warn if session cost exceeds threshold.

    Fires on ``PRE_TOOL_USE``.  Never blocks — just surfaces a message.
    """
    if _session_cost_usd >= _COST_THRESHOLD_USD:
        return HookResult(
            proceed=True,
            message=(f"[cost] Session spend ${_session_cost_usd:.2f} exceeds ${_COST_THRESHOLD_USD:.2f} threshold."),
        )
    return HookResult()


# ----------------------------------------------------------------------- #
#  Git dirty-tree warning
# ----------------------------------------------------------------------- #


async def git_dirty_warning_hook(**kwargs: Any) -> HookResult:
    """On ``SESSION_START``, warn if the working tree has uncommitted changes.

    Uses ``git status --porcelain`` — if output is non-empty the tree is
    dirty.  Gracefully returns an empty result if git is unavailable or
    the cwd is not a repo.
    """
    git_exe = shutil.which("git")
    if git_exe is None:
        return HookResult()

    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "status",
            "--porcelain",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except Exception:
        return HookResult()

    if proc.returncode != 0:
        return HookResult()

    output = stdout.decode(errors="replace").strip()
    if output:
        line_count = len(output.splitlines())
        return HookResult(
            proceed=True,
            message=f"[git] Working tree is dirty ({line_count} changed file(s)).",
        )

    return HookResult()


# ----------------------------------------------------------------------- #
#  Auto-save memory (stub)
# ----------------------------------------------------------------------- #


async def auto_save_memory_hook(response: str = "", **kwargs: Any) -> HookResult:
    """After each assistant response, check if something should be memorized.

    Delegates to MemoryManager.auto_extract() which scans the response
    for explicit "remember" requests and implicit memory-worthy patterns
    (user corrections, project facts, preferences).
    """
    if not response:
        return HookResult()

    try:
        from karna.memory import MemoryManager

        cwd = Path.cwd()
        mm = MemoryManager(cwd)
        saved = mm.auto_extract(response)
        if saved:
            return HookResult(
                proceed=True,
                message=f"[memory] Saved {len(saved)} memory entry(ies): {', '.join(e.name for e in saved)}",
            )
    except Exception as exc:
        logger.debug("auto_save_memory_hook failed: %s", exc)

    return HookResult()
