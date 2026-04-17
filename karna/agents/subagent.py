"""Subagent system — independent agents with their own conversation context.

A SubAgent is a lightweight agent that runs in the background with its own
conversation history, tools, and optional git worktree isolation. The
SubAgentManager tracks all spawned agents and provides lookup / listing.

Ported from cc-src teammate/agent patterns with attribution to the
Anthropic Claude Code codebase.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import shutil
from typing import Any, Literal
from uuid import uuid4

from karna.agents.loop import agent_loop_sync
from karna.models import Conversation, Message
from karna.providers.base import BaseProvider
from karna.tools.base import BaseTool

logger = logging.getLogger(__name__)


class SubAgent:
    """Independent agent with its own conversation context.

    Each subagent has:
    - A unique name (used as key in the manager)
    - Its own Conversation (message history)
    - A set of tools it can use
    - A system prompt
    - An optional worktree for filesystem isolation
    """

    def __init__(
        self,
        name: str,
        provider: BaseProvider,
        tools: list[BaseTool],
        system_prompt: str,
        isolation: str = "none",
    ) -> None:
        self.name = name
        self.provider = provider
        self.tools = tools
        self.system_prompt = system_prompt
        self.isolation = isolation

        self.conversation = Conversation()
        self.status: Literal["pending", "running", "completed", "failed"] = "pending"
        self.result: str = ""
        self.error: str = ""
        self._task: asyncio.Task[str] | None = None

        # Worktree state (populated when isolation="worktree")
        self.worktree_path: str | None = None
        self.worktree_branch: str | None = None

    # ------------------------------------------------------------------ #
    #  Worktree lifecycle
    # ------------------------------------------------------------------ #

    def _setup_worktree(self) -> str:
        """Create a git worktree for isolated filesystem access.

        Returns the worktree path. Raises on failure.
        """
        worktree_path = f"/tmp/karna-worktree-{self.name}"
        branch_name = f"subagent/{self.name}"

        try:
            # Create the worktree with a new branch
            subprocess.run(
                ["git", "worktree", "add", worktree_path, "-b", branch_name],
                check=True,
                capture_output=True,
                text=True,
            )
            self.worktree_path = worktree_path
            self.worktree_branch = branch_name
            logger.info(
                "Created worktree for subagent %s at %s (branch %s)",
                self.name, worktree_path, branch_name,
            )
            return worktree_path
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"Failed to create worktree for subagent {self.name}: {exc.stderr}"
            ) from exc

    def _cleanup_worktree(self) -> None:
        """Remove the git worktree and its branch."""
        if not self.worktree_path:
            return
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", self.worktree_path],
                check=False,
                capture_output=True,
            )
            logger.info("Removed worktree at %s", self.worktree_path)
        except Exception:
            logger.warning("Failed to remove worktree at %s", self.worktree_path)

        if self.worktree_branch:
            try:
                subprocess.run(
                    ["git", "branch", "-D", self.worktree_branch],
                    check=False,
                    capture_output=True,
                )
            except Exception:
                logger.warning(
                    "Failed to delete worktree branch %s", self.worktree_branch
                )

    # ------------------------------------------------------------------ #
    #  Execution
    # ------------------------------------------------------------------ #

    async def run(self, prompt: str) -> str:
        """Run the subagent to completion. Returns final response text.

        Sets up worktree if ``isolation='worktree'``, runs the agent loop,
        and cleans up on completion.
        """
        self.status = "running"

        # Set up worktree isolation if requested
        if self.isolation == "worktree":
            try:
                self._setup_worktree()
            except RuntimeError as exc:
                self.status = "failed"
                self.error = str(exc)
                return f"[error] {exc}"

        # Seed the conversation with the user prompt
        self.conversation.messages.append(
            Message(role="user", content=prompt)
        )

        try:
            final_message = await agent_loop_sync(
                self.provider,
                self.conversation,
                self.tools,
                system_prompt=self.system_prompt,
                max_iterations=25,
            )
            self.result = final_message.content
            self.status = "completed"
            return self.result
        except Exception as exc:
            self.status = "failed"
            self.error = str(exc)
            logger.exception("Subagent %s failed: %s", self.name, exc)
            return f"[error] Subagent {self.name} failed: {exc}"
        finally:
            if self.isolation == "worktree":
                self._cleanup_worktree()

    async def run_in_background(self, prompt: str) -> asyncio.Task[str]:
        """Run asynchronously. Returns an asyncio.Task the caller can await."""
        self._task = asyncio.create_task(
            self.run(prompt), name=f"subagent-{self.name}"
        )
        return self._task

    # ------------------------------------------------------------------ #
    #  Introspection
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict[str, Any]:
        """Serialise agent state for status reporting."""
        return {
            "name": self.name,
            "status": self.status,
            "isolation": self.isolation,
            "worktree_path": self.worktree_path,
            "result_preview": self.result[:200] if self.result else None,
            "error": self.error or None,
        }


class SubAgentManager:
    """Registry of spawned subagents.

    Provides spawn, get, and listing functionality so the parent agent
    can track and query its children.
    """

    def __init__(self) -> None:
        self.agents: dict[str, SubAgent] = {}

    def spawn(
        self,
        name: str,
        provider: BaseProvider,
        tools: list[BaseTool],
        system_prompt: str,
        isolation: str = "none",
    ) -> SubAgent:
        """Create and register a new SubAgent.

        Raises ``ValueError`` if an agent with the same name already exists
        and is still running.
        """
        if name in self.agents:
            existing = self.agents[name]
            if existing.status in ("pending", "running"):
                raise ValueError(
                    f"Subagent {name!r} already exists and is {existing.status}. "
                    f"Wait for it to finish or use a different name."
                )
            # Replace completed/failed agent
            logger.info("Replacing finished subagent %s (was %s)", name, existing.status)

        agent = SubAgent(
            name=name,
            provider=provider,
            tools=tools,
            system_prompt=system_prompt,
            isolation=isolation,
        )
        self.agents[name] = agent
        return agent

    def get(self, name: str) -> SubAgent | None:
        """Look up a subagent by name."""
        return self.agents.get(name)

    def list_active(self) -> list[SubAgent]:
        """Return all agents that are pending or running."""
        return [a for a in self.agents.values() if a.status in ("pending", "running")]

    def list_all(self) -> list[SubAgent]:
        """Return all agents regardless of status."""
        return list(self.agents.values())
