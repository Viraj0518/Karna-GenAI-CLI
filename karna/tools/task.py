"""Task tool — spawn a background subagent to handle a complex subtask.

The parent agent uses this tool to delegate work to an independent
subagent that runs with its own conversation context, tools, and
optional git worktree isolation.

Ported from cc-src AgentTool / in-process teammate patterns with
attribution to the Anthropic Claude Code codebase.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from karna.agents.subagent import SubAgentManager
from karna.tools.base import BaseTool

logger = logging.getLogger(__name__)

# Module-level manager so all TaskTool instances share state.
_manager = SubAgentManager()


def get_subagent_manager() -> SubAgentManager:
    """Return the global SubAgentManager singleton."""
    return _manager


class TaskTool(BaseTool):
    """Spawn a background subagent to handle a complex subtask independently.

    The subagent runs with its own conversation context and tool set.
    It can optionally use a git worktree for filesystem isolation.

    Returns immediately with the subagent name and status. The parent
    can query progress later.
    """

    name = "task"
    description = (
        "Spawn a background subagent to handle a complex subtask independently. "
        "Returns immediately with the agent ID. The subagent runs asynchronously "
        "with its own conversation context."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "What the subagent should do (short summary).",
            },
            "prompt": {
                "type": "string",
                "description": "Full prompt for the subagent.",
            },
            "model": {
                "type": "string",
                "description": "Model override (optional). Uses parent model if omitted.",
            },
            "isolation": {
                "type": "string",
                "enum": ["none", "worktree"],
                "description": "Isolation mode. 'worktree' creates a git worktree.",
            },
        },
        "required": ["description", "prompt"],
    }

    def __init__(
        self,
        *,
        provider: Any | None = None,
        tools: list[BaseTool] | None = None,
        system_prompt: str | None = None,
    ) -> None:
        super().__init__()
        self._provider = provider
        self._tools = tools or []
        self._system_prompt = system_prompt or (
            "You are a subagent. Complete the assigned task thoroughly and report back."
        )

    async def execute(self, **kwargs: Any) -> str:
        description: str = kwargs["description"]
        prompt: str = kwargs["prompt"]
        isolation: str = kwargs.get("isolation", "none")

        if self._provider is None:
            return "[error] TaskTool has no provider configured. Set provider when instantiating TaskTool."

        # Generate a unique agent name
        agent_id = uuid4().hex[:8]
        agent_name = f"agent-{agent_id}"

        try:
            agent = _manager.spawn(
                name=agent_name,
                provider=self._provider,
                tools=self._tools,
                system_prompt=self._system_prompt,
                isolation=isolation,
            )
        except ValueError as exc:
            return f"[error] {exc}"

        # Launch in background
        await agent.run_in_background(prompt)

        logger.info(
            "Spawned subagent %s: %s (isolation=%s)",
            agent_name,
            description,
            isolation,
        )

        return (
            f"Subagent '{agent_name}' spawned for: {description}\n"
            f"Isolation: {isolation}\n"
            f"Status: {agent.status}\n"
            f"The subagent is running in the background."
        )
