"""Plan mode — "think first, don't execute".

Powers the ``/plan`` slash command. The model is run with a
restricted read-only toolset and an injected system prompt that
tells it to produce a numbered plan rather than execute anything.

The restricted toolset is deliberately tiny: ``read``, ``grep``,
and ``glob`` only. Anything that can write to the filesystem, run
shell commands, hit the network, or otherwise mutate state is
filtered out *before* the provider ever sees the tool definitions,
so even a misbehaving model cannot escape the sandbox. (The
3-tier permission check in :mod:`karna.agents.loop` would catch
misuse anyway, but defence-in-depth is cheap.)

After planning the user can approve with ``/do`` (which re-runs
the normal agent loop using the plan text as the prompt) or
refine with another ``/plan <revised-goal>``.
"""

from __future__ import annotations

import logging

from karna.agents.loop import agent_loop_sync
from karna.models import Conversation, Message
from karna.providers.base import BaseProvider
from karna.tools.base import BaseTool

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Plan-mode system prompt
# --------------------------------------------------------------------------- #

PLAN_MODE_SYSTEM_PROMPT = """\
You are in plan mode. Your job: analyze the task and output a numbered plan.
Do NOT execute any tools that modify state. You may use read/grep/glob to
investigate. Output a clear step-by-step plan. End with:
"Plan ready. Approve with /do or revise with /plan again."
"""


# --------------------------------------------------------------------------- #
#  Tool allow-list
# --------------------------------------------------------------------------- #

# Only these tools are exposed while in plan mode.
READ_ONLY_TOOLS: frozenset[str] = frozenset({"read", "grep", "glob"})


def filter_tools_for_plan_mode(tools: list[BaseTool]) -> list[BaseTool]:
    """Return the subset of *tools* that are safe in plan mode.

    Matching is by ``tool.name`` so tools can be reordered or
    wrapped without breaking the allow-list. Tools with no ``name``
    attribute (shouldn't happen — ``BaseTool`` requires one) are
    dropped for safety.
    """
    kept: list[BaseTool] = []
    for t in tools:
        name = getattr(t, "name", "")
        if isinstance(name, str) and name in READ_ONLY_TOOLS:
            kept.append(t)
    return kept


def _compose_system_prompt(base: str | None) -> str:
    """Stack the user's configured system prompt with the plan-mode prompt.

    Plan-mode instructions come *last* so they take precedence over
    anything the user's system prompt might imply about execution.
    """
    if not base:
        return PLAN_MODE_SYSTEM_PROMPT
    return f"{base.rstrip()}\n\n{PLAN_MODE_SYSTEM_PROMPT}"


# --------------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------------- #


async def run_plan_mode(
    goal: str,
    provider: BaseProvider,
    tools: list[BaseTool],
    model: str,
    *,
    base_system_prompt: str | None = None,
    max_iterations: int = 10,
) -> str:
    """Run the agent loop in plan mode and return the plan text.

    Parameters
    ----------
    goal:
        What the user wants planned (the text after ``/plan``).
    provider:
        Initialised provider with ``model`` already configured.
    tools:
        Full tool set. Will be filtered to ``READ_ONLY_TOOLS``
        before being handed to the agent loop.
    model:
        Kept for caller telemetry — the provider instance itself
        is the source of truth for the active model.
    base_system_prompt:
        The user's regular system prompt, if any. The plan-mode
        instructions are appended to it so user-configured
        personality/conventions still apply.
    max_iterations:
        Cap on inner-loop iterations. Plan mode rarely needs many —
        the model reads a few files, then emits the plan.

    Returns
    -------
    str
        The assistant's plan text. The caller is expected to
        persist this so ``/do`` can later re-use it.
    """
    _ = model  # captured for logging symmetry with run_autonomous_loop

    safe_tools = filter_tools_for_plan_mode(tools)
    system_prompt = _compose_system_prompt(base_system_prompt)

    conversation = Conversation()
    conversation.messages.append(Message(role="user", content=goal))

    logger.debug(
        "plan mode: %d/%d tools exposed (%s)",
        len(safe_tools),
        len(tools),
        [t.name for t in safe_tools],
    )

    final: Message = await agent_loop_sync(
        provider,
        conversation,
        safe_tools,
        system_prompt=system_prompt,
        max_iterations=max_iterations,
    )
    return final.content or ""


__all__ = [
    "PLAN_MODE_SYSTEM_PROMPT",
    "READ_ONLY_TOOLS",
    "filter_tools_for_plan_mode",
    "run_plan_mode",
]
