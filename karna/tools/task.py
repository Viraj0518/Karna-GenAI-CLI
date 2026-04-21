"""Task tool — spawn, continue, and manage subagents.

The parent agent uses this tool to delegate work to an independent
subagent that runs with its own conversation context, tools, and
optional git worktree isolation. Supports three actions:

- ``create`` — spawn a new subagent (foreground or background)
- ``send_message`` — continue a completed/running subagent with new instructions
- ``stop`` — cancel a running subagent

The subagent runs synchronously within ``execute`` (foreground) or
returns immediately with an agent ID (background). Background
completion notifications are injected into the parent conversation
by the agent loop.

Ported from cc-src AgentTool / in-process teammate patterns with
attribution to the Anthropic Claude Code codebase.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from karna.agents.subagent import SubAgentManager, spawn_subagent
from karna.prompts.cc_tool_prompts import CC_TOOL_PROMPTS
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
    """Spawn, continue, or stop subagents.

    Supports three actions:
    - ``create`` (default) — spawn a new subagent
    - ``send_message`` — continue an existing subagent with new instructions
    - ``stop`` — cancel a running subagent

    Create mode supports foreground (awaits result) and background
    (returns agent ID immediately, notifies on completion).
    """

    name = "task"
    description = (
        "Manage subagents: create new ones, send follow-up messages to "
        "existing ones, or stop running ones. Subagents run with their "
        "own conversation context and filtered tool subset."
    )
    cc_prompt = CC_TOOL_PROMPTS["task"]
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "send_message", "stop"],
                "description": (
                    "Action to perform. 'create' spawns a new subagent, "
                    "'send_message' continues an existing one, "
                    "'stop' cancels a running one. Default: 'create'."
                ),
            },
            "description": {
                "type": "string",
                "description": "What the subagent should do (short summary for logs). Required for 'create'.",
            },
            "prompt": {
                "type": "string",
                "description": "Full prompt for the subagent. Required for 'create'.",
            },
            "agent_id": {
                "type": "string",
                "description": "Agent ID or name. Required for 'send_message' and 'stop'.",
            },
            "message": {
                "type": "string",
                "description": "Message to send to an existing agent. Required for 'send_message'.",
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
                "description": (
                    "If true, the subagent runs in the background and returns "
                    "its agent ID immediately. Completion is notified via a "
                    "system message. Default: false (foreground)."
                ),
            },
        },
        "required": [],
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
        action: str = kwargs.get("action", "create")

        if action == "send_message":
            if not kwargs.get("agent_id"):
                return "[error] 'agent_id' is required for send_message action."
            if not kwargs.get("message"):
                return "[error] 'message' is required for send_message action."
            return await self._handle_send_message(**kwargs)
        elif action == "stop":
            if not kwargs.get("agent_id"):
                return "[error] 'agent_id' is required for stop action."
            return await self._handle_stop(**kwargs)
        else:
            if not kwargs.get("description"):
                return "[error] 'description' is required for create action."
            if not kwargs.get("prompt"):
                return "[error] 'prompt' is required for create action."
            return await self._handle_create(**kwargs)

    async def _handle_create(self, **kwargs: Any) -> str:
        """Spawn a new subagent (foreground or background)."""
        description: str = kwargs["description"]
        prompt: str = kwargs["prompt"]
        subagent_type: str = kwargs.get("subagent_type", "general")
        tool_names: list[str] | None = kwargs.get("tools")
        isolation: Literal["none", "worktree"] = kwargs.get("isolation", "none")
        model: str | None = kwargs.get("model")
        max_iterations: int = int(kwargs.get("max_iterations", 20))
        background: bool = kwargs.get("run_in_background", False)

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
            background,
        )

        if not background:
            # --- Foreground: await the subagent result -------------------
            result = await spawn_subagent(
                prompt,
                parent_config=self._parent_config,
                parent_provider=self._provider,
                tools=selected_tools,
                model=model,
                max_iterations=max_iterations,
                isolation=isolation,
                worktree_base=self._worktree_base,
                system_prompt=self._system_prompt,
            )
            return result

        # --- Background: use the managed SubAgent class -----------------
        manager = get_subagent_manager()
        # Use description as agent name, sanitised
        agent_name = description.replace(" ", "_")[:40]
        try:
            agent = manager.spawn(
                name=agent_name,
                provider=self._provider,
                tools=selected_tools,
                system_prompt=self._system_prompt
                or getattr(
                    self._parent_config,
                    "system_prompt",
                    "You are a subagent. Complete the assigned task thoroughly and report back.",
                ),
                isolation=isolation,
            )
        except ValueError as exc:
            return f"[error] {exc}"

        await agent.run_in_background(prompt, max_iterations=max_iterations)

        return json.dumps(
            {
                "status": "started",
                "agent_id": agent.agent_id,
                "agent_name": agent.name,
                "message": f"Subagent '{agent.name}' started in background. You will be notified when it completes.",
            }
        )

    async def _handle_send_message(self, **kwargs: Any) -> str:
        """Send a message to an existing subagent (E4)."""
        agent_id_or_name: str | None = kwargs.get("agent_id")
        message: str | None = kwargs.get("message")

        if not agent_id_or_name:
            return "[error] 'agent_id' is required for send_message action."
        if not message:
            return "[error] 'message' is required for send_message action."

        manager = get_subagent_manager()
        return await manager.send_message(agent_id_or_name, message)

    async def _handle_stop(self, **kwargs: Any) -> str:
        """Stop a running subagent."""
        agent_id_or_name: str | None = kwargs.get("agent_id")

        if not agent_id_or_name:
            return "[error] 'agent_id' is required for stop action."

        manager = get_subagent_manager()
        agent = manager._resolve_agent(agent_id_or_name)
        if agent is None:
            return f"[error] No subagent found with id/name: {agent_id_or_name}"

        if agent.status != "running":
            return f"[error] Subagent {agent.name} is not running (status: {agent.status})."

        if agent._task is not None:
            agent._task.cancel()
            agent.status = "failed"
            agent.error = "Cancelled by parent"
            return f"Subagent {agent.name} cancelled."

        return f"[error] Subagent {agent.name} has no task to cancel."
