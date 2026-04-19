"""Task tool — spawn a subagent to handle a complex subtask.

The parent agent uses this tool to delegate work to an independent
subagent that runs with its own conversation context, tools, and
optional git worktree isolation. The subagent runs synchronously
within ``execute`` and its final assistant message is returned as
the tool result.

Ported from cc-src AgentTool / in-process teammate patterns with
attribution to the Anthropic Claude Code codebase.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from karna.agents.subagent import SubAgentManager
from karna.tools.base import BaseTool

logger = logging.getLogger(__name__)

# Valid values for the ``subagent_type`` parameter. Each type maps to a
# filter applied to the parent's tool registry — see ``_filter_tools_for_type``.
_VALID_SUBAGENT_TYPES = {"general", "research", "code"}

# Tools considered "dangerous" (mutate state, run shell, or hit the
# network). These are excluded from the default ``general`` subset.
_DANGEROUS_TOOL_NAMES = {"bash", "write", "edit", "git"}

# Read-only / information-gathering tools (used for the ``research`` type).
_RESEARCH_TOOL_NAMES = {"read", "grep", "glob", "web_search", "web_fetch", "task"}

# Module-level manager retained for backwards-compatibility with the
# long-running ``SubAgent`` API. New code should prefer ``spawn_subagent``.
_manager = SubAgentManager()


def get_subagent_manager() -> SubAgentManager:
    """Return the global SubAgentManager singleton (legacy API)."""
    return _manager


def _filter_tools_for_type(
    all_tools: list[BaseTool],
    subagent_type: str,
    override_names: list[str] | None,
) -> list[BaseTool]:
    """Resolve the tool subset for a given subagent_type + override.

    If *override_names* is provided, it wins — only tools whose names
    appear in it are returned (the type is ignored).

    Otherwise:
    - ``general``: everything except dangerous tools
    - ``research``: read/grep/glob/web_search/web_fetch/task only
    - ``code``: all tools (caller trusts the subagent to mutate)
    """
    if override_names is not None:
        wanted = set(override_names)
        return [t for t in all_tools if t.name in wanted]

    if subagent_type == "code":
        return list(all_tools)
    if subagent_type == "research":
        return [t for t in all_tools if t.name in _RESEARCH_TOOL_NAMES]
    # default: general
    return [t for t in all_tools if t.name not in _DANGEROUS_TOOL_NAMES]


class TaskTool(BaseTool):
    """Spawn a subagent to handle a complex subtask.

    The subagent runs with its own conversation context and tool set.
    It can optionally use a git worktree for filesystem isolation.
    The tool blocks until the subagent completes and returns the final
    assistant message content as its result.
    """

    name = "task"
    description = (
        "Spawn a subagent to handle a complex subtask independently. "
        "The subagent runs with its own conversation context and a "
        "filtered tool subset. Returns the subagent's final answer."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "What the subagent should do (short summary for logs).",
            },
            "prompt": {
                "type": "string",
                "description": "Full prompt for the subagent.",
            },
            "subagent_type": {
                "type": "string",
                "enum": sorted(_VALID_SUBAGENT_TYPES),
                "description": (
                    "Tool subset preset: 'general' (default, excludes "
                    "bash/write/edit/git), 'research' (read-only), or "
                    "'code' (all tools)."
                ),
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Explicit list of tool names to expose. Overrides subagent_type.",
            },
            "isolation": {
                "type": "string",
                "enum": ["none", "worktree"],
                "description": "Isolation mode. 'worktree' creates a git worktree.",
            },
            "model": {
                "type": "string",
                "description": "Model override (optional). Uses parent model if omitted.",
            },
            "max_iterations": {
                "type": "integer",
                "description": "Upper bound on agent-loop iterations. Default 20.",
                "minimum": 1,
                "maximum": 100,
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Run the subagent in the background. Default True.",
            },
            "action": {
                "type": "string",
                "enum": ["spawn", "stop", "send_message"],
                "description": "Action to perform. Default 'spawn'.",
            },
            "agent_id": {
                "type": "string",
                "description": "Agent ID for stop/send_message actions.",
            },
            "message": {
                "type": "string",
                "description": "Message to send (for send_message action).",
            },
        },
        "required": ["description", "prompt"],
    }

    def __init__(
        self,
        *,
        provider: Any | None = None,
        parent_config: Any | None = None,
        tools: list[BaseTool] | None = None,
        worktree_base: Path | None = None,
        system_prompt: str | None = None,
    ) -> None:
        super().__init__()
        self._provider = provider
        self._parent_config = parent_config
        self._all_tools = list(tools) if tools is not None else []
        self._worktree_base = worktree_base
        self._system_prompt = system_prompt

    async def execute(self, **kwargs: Any) -> str:
        action: str = kwargs.get("action", "spawn")

        # --- Non-spawn actions -------------------------------------------
        if action == "stop":
            agent_id = kwargs.get("agent_id")
            if not agent_id:
                return "[error] action 'stop' requires agent_id"
            agent = _manager._resolve_agent(agent_id)
            if agent is None:
                return f"[error] No agent found with id or name {agent_id!r}"
            if agent.status in ("completed", "failed"):
                return f"[error] Agent {agent_id!r} already {agent.status}"
            if agent._task is not None:
                agent._task.cancel()
            agent.status = "failed"
            agent.error = "Stopped by user"
            return f"Agent {agent_id!r} stopped."

        if action == "send_message":
            agent_id = kwargs.get("agent_id")
            if not agent_id:
                return "[error] action 'send_message' requires agent_id"
            message = kwargs.get("message")
            if not message:
                return "[error] action 'send_message' requires message"
            return await _manager.send_message(agent_id, message)

        # --- Spawn action ------------------------------------------------
        description: str = kwargs["description"]
        prompt: str = kwargs["prompt"]
        subagent_type: str = kwargs.get("subagent_type", "general")
        tool_names: list[str] | None = kwargs.get("tools")
        isolation: Literal["none", "worktree"] = kwargs.get("isolation", "none")
        _model: str | None = kwargs.get("model")  # noqa: F841 — reserved for model override
        max_iterations: int = int(kwargs.get("max_iterations", 20))
        run_in_background: bool = kwargs.get("run_in_background", True)

        # --- Validation --------------------------------------------------
        if subagent_type not in _VALID_SUBAGENT_TYPES:
            raise ValueError(
                f"Invalid subagent_type {subagent_type!r}. Must be one of: {sorted(_VALID_SUBAGENT_TYPES)}"
            )

        if isolation not in ("none", "worktree"):
            return f"[error] Invalid isolation {isolation!r}. Must be 'none' or 'worktree'."

        if self._provider is None:
            return (
                "[error] TaskTool has no provider configured. "
                "Instantiate TaskTool with provider=<BaseProvider> before use."
            )

        # --- Resolve tool subset ----------------------------------------
        selected_tools = _filter_tools_for_type(
            self._all_tools,
            subagent_type,
            tool_names,
        )

        logger.info(
            "TaskTool spawning subagent: %s (type=%s, tools=%d, isolation=%s, bg=%s)",
            description,
            subagent_type,
            len(selected_tools),
            isolation,
            run_in_background,
        )

        if not run_in_background:
            # --- Foreground: use the legacy manager API -------------------
            agent_name = description.replace(" ", "_")
            agent = _manager.spawn(
                name=agent_name,
                provider=self._provider,
                tools=selected_tools,
                system_prompt=self._system_prompt or "You are a helpful assistant.",
                isolation=isolation,
                max_iterations=max_iterations,
            )
            result = await agent.run(prompt)
            return result

        # --- Background: spawn and return immediately -------------------
        agent_name = description.replace(" ", "_")
        agent = _manager.spawn(
            name=agent_name,
            provider=self._provider,
            tools=selected_tools,
            system_prompt=self._system_prompt or "You are a helpful assistant.",
            isolation=isolation,
            max_iterations=max_iterations,
        )
        await agent.run_in_background(prompt)

        return json.dumps({
            "status": "started",
            "agent_id": agent.agent_id,
            "agent_name": agent_name,
        })
