"""Subagent system — independent agents with their own conversation context.

A SubAgent is a lightweight agent that runs in the background with its own
conversation history, tools, and optional git worktree isolation. The
SubAgentManager tracks all spawned agents and provides lookup / listing.

The canonical entrypoint for one-shot subagent runs is :func:`spawn_subagent`,
which returns the final assistant content as a string. The legacy
``SubAgent`` / ``SubAgentManager`` classes are retained for long-running
background agents tracked by name.

Ported from cc-src teammate/agent patterns with attribution to the
Anthropic Claude Code codebase.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Literal
from uuid import uuid4

from karna.agents.loop import agent_loop_sync
from karna.models import Conversation, Message
from karna.providers.base import BaseProvider
from karna.tools.base import BaseTool

logger = logging.getLogger(__name__)

# Serialise worktree creation so concurrent spawns don't race on `git worktree add`.
_WORKTREE_LOCK = asyncio.Lock()


# --------------------------------------------------------------------------- #
#  Worktree isolation helpers
# --------------------------------------------------------------------------- #


def _is_git_repo(path: Path) -> bool:
    """Return True if *path* is inside a git working tree."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (FileNotFoundError, OSError):
        return False


def _git_worktree_add(parent_cwd: Path, worktree_path: Path) -> bool:
    """Create a git worktree at *worktree_path* based on HEAD of *parent_cwd*.

    Returns True on success, False on failure (logged as warning).
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(parent_cwd), "worktree", "add", str(worktree_path), "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            logger.info("Created worktree at %s", worktree_path)
            return True
        logger.warning(
            "git worktree add failed (%d): %s",
            result.returncode,
            (result.stderr or result.stdout or "").strip(),
        )
        return False
    except (FileNotFoundError, OSError) as exc:
        logger.warning("git worktree add errored: %s", exc)
        return False


def _git_worktree_remove(parent_cwd: Path, worktree_path: Path) -> None:
    """Remove a git worktree, with a shutil.rmtree fallback.

    Failures are logged, never raised — cleanup is best-effort.
    """
    removed_by_git = False
    try:
        result = subprocess.run(
            ["git", "-C", str(parent_cwd), "worktree", "remove", "--force", str(worktree_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            removed_by_git = True
            logger.info("Removed worktree at %s", worktree_path)
        else:
            logger.warning(
                "git worktree remove exited %d for %s: %s",
                result.returncode,
                worktree_path,
                (result.stderr or result.stdout or "").strip(),
            )
    except (FileNotFoundError, OSError) as exc:
        logger.warning("git worktree remove errored for %s: %s", worktree_path, exc)

    if not removed_by_git and worktree_path.exists():
        try:
            shutil.rmtree(worktree_path, ignore_errors=False)
            logger.info("Fallback rmtree removed worktree dir %s", worktree_path)
        except OSError as exc:
            logger.error("Failed to rmtree worktree dir %s: %s", worktree_path, exc)


def _has_worktree_changes(path: Path) -> bool:
    """Return True if *path* is a git repo with uncommitted changes (staged, unstaged, or untracked)."""
    if not _is_git_repo(path):
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (FileNotFoundError, OSError):
        return False


@asynccontextmanager
async def _isolation_context(
    isolation: Literal["none", "worktree"],
    worktree_base: Path | None,
) -> AsyncIterator[Path]:
    """Yield the cwd the subagent should run in.

    For ``isolation="none"`` this is simply the current cwd.

    For ``isolation="worktree"`` a fresh worktree is created under
    ``worktree_base`` (defaulting to the system temp dir). The worktree
    is removed on exit, even if the body raises. If the current cwd is
    not a git repo, we log a warning and fall back to no isolation
    instead of crashing.
    """
    original_cwd = Path(os.getcwd())

    if isolation == "none":
        yield original_cwd
        return

    # isolation == "worktree"
    if not _is_git_repo(original_cwd):
        logger.warning(
            "spawn_subagent: parent cwd %s is not a git repo; falling back to isolation='none'",
            original_cwd,
        )
        yield original_cwd
        return

    base = worktree_base or Path(tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)
    worktree_path = base / f"karna-worktree-{uuid4().hex[:8]}"

    # Serialise creation so concurrent spawns don't race on the same git index.
    async with _WORKTREE_LOCK:
        created = _git_worktree_add(original_cwd, worktree_path)

    if not created:
        logger.warning(
            "spawn_subagent: failed to create worktree at %s; falling back to isolation='none'",
            worktree_path,
        )
        yield original_cwd
        return

    try:
        # Change cwd for the duration of the subagent run.
        os.chdir(worktree_path)
        yield worktree_path
    finally:
        # Restore cwd before cleanup so we don't delete the dir we're standing in.
        try:
            os.chdir(original_cwd)
        except OSError:
            pass
        _git_worktree_remove(original_cwd, worktree_path)


# --------------------------------------------------------------------------- #
#  Primary entrypoint
# --------------------------------------------------------------------------- #


async def spawn_subagent(
    prompt: str,
    *,
    parent_config: Any,
    parent_provider: BaseProvider,
    tools: list[BaseTool],
    model: str | None = None,
    max_iterations: int = 20,
    isolation: Literal["none", "worktree"] = "none",
    worktree_base: Path | None = None,
    system_prompt: str | None = None,
) -> str:
    """Run a one-shot subagent and return the final assistant content.

    Parameters
    ----------
    prompt
        The user task for the subagent.
    parent_config
        Parent :class:`~karna.config.KarnaConfig` — used to seed defaults
        (system prompt, max_tokens, temperature) when not overridden.
    parent_provider
        Provider instance; reused directly so credentials are inherited.
    tools
        Tools the subagent may call. Pass a filtered subset to sandbox.
    model
        Optional model override. Currently informational — the provider
        dictates the real model choice. Reserved for future use.
    max_iterations
        Hard cap on agent-loop iterations.
    isolation
        ``"none"`` runs in the parent cwd. ``"worktree"`` creates a git
        worktree and runs there, falling back to ``"none"`` with a
        warning if the parent cwd isn't a git repo.
    worktree_base
        Directory under which worktrees are created. Defaults to the
        system temp dir.
    system_prompt
        Override for the subagent's system prompt. If None, falls back
        to ``parent_config.system_prompt`` (if present) or a default.

    Returns
    -------
    str
        The final assistant message content.  Never raises for normal
        operation — errors are returned as ``[error] ...`` strings so
        the caller can surface them without try/except boilerplate.
    """
    # Resolve system prompt
    if system_prompt is None:
        system_prompt = getattr(
            parent_config,
            "system_prompt",
            "You are a subagent. Complete the assigned task thoroughly and report back.",
        )

    # Resolve completion-side defaults from parent_config when available
    max_tokens = getattr(parent_config, "max_tokens", None)
    temperature = getattr(parent_config, "temperature", None)

    conversation = Conversation(messages=[Message(role="user", content=prompt)])

    # model is informational for now; log so operators can trace overrides
    if model is not None:
        logger.debug("spawn_subagent: model override requested: %s", model)

    try:
        async with _isolation_context(isolation, worktree_base):
            final_message = await agent_loop_sync(
                parent_provider,
                conversation,
                tools,
                system_prompt=system_prompt,
                max_iterations=max_iterations,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        return final_message.content
    except Exception as exc:
        logger.exception("spawn_subagent failed: %s", exc)
        return f"[error] Subagent failed: {type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- #
#  Legacy long-running subagent support
# --------------------------------------------------------------------------- #


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
        max_iterations: int = 25,
    ) -> None:
        self.name = name
        self.provider = provider
        self.tools = tools
        self.system_prompt = system_prompt
        self.isolation = isolation
        self.max_iterations = max_iterations

        self.conversation = Conversation()
        self.status: Literal["pending", "running", "completed", "failed"] = "pending"
        self.result: str = ""
        self.error: str = ""
        self._task: asyncio.Task[str] | None = None
        self.agent_id: str = uuid4().hex[:12]
        self._completion_callbacks: list = []
        self._queued_messages: list[str] = []
        self._parent_cwd: str = os.getcwd()

        # Worktree state (populated when isolation="worktree")
        self.worktree_path: str | None = None
        self.worktree_branch: str | None = None
        self.worktree_preserved: bool = False

    async def continue_with(self, message: str) -> str:
        """Send a follow-up message to this agent.

        If the agent is running, the message is queued and will be processed
        after the current turn. Returns a "[queued]" acknowledgement.

        If the agent is completed or failed, re-runs with the new message.
        """
        if self.status == "running":
            self._queued_messages.append(message)
            return f"[queued] Message queued for agent {self.name!r}"
        # Re-run with new message
        self.conversation.messages.append(Message(role="user", content=message))
        return await self.run(message)

    def on_complete(self, callback) -> None:
        """Register a callback to fire when the agent completes (success or failure)."""
        self._completion_callbacks.append(callback)

    def _fire_completion_callbacks(self) -> None:
        """Invoke all registered completion callbacks."""
        for cb in self._completion_callbacks:
            try:
                cb(self)
            except Exception:  # noqa: BLE001
                logger.warning("Completion callback failed for %s", self.name, exc_info=True)

    # ------------------------------------------------------------------ #
    #  Worktree lifecycle
    # ------------------------------------------------------------------ #

    def _setup_worktree(self) -> str:
        """Create a git worktree for isolated filesystem access.

        Returns the worktree path. Raises on failure.

        A short uuid suffix is appended to the path AND the branch so
        parallel subagents with the same logical name can coexist
        without stepping on each other's checkouts.
        """
        unique = uuid.uuid4().hex[:8]
        worktree_path = str(Path(tempfile.gettempdir()) / f"karna-worktree-{self.name}-{unique}")
        branch_name = f"subagent/{self.name}-{unique}"

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
                self.name,
                worktree_path,
                branch_name,
            )
            return worktree_path
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Failed to create worktree for subagent {self.name}: {exc.stderr}") from exc

    def _cleanup_worktree(self, force: bool = False) -> None:
        """Remove the git worktree and its branch.

        If the agent completed successfully and there are uncommitted
        changes, the worktree is preserved (``worktree_preserved`` is
        set to ``True``).  On failure or when *force* is ``True``, the
        worktree is always removed.

        Failures are logged explicitly — never silently ignored.  Tries
        ``git worktree remove`` first, then falls back to
        ``shutil.rmtree`` if git leaves the directory behind.
        """
        if not self.worktree_path:
            return

        # Preserve worktree if agent succeeded and there are changes
        if not force and self.status == "completed" and _has_worktree_changes(Path(self.worktree_path)):
            self.worktree_preserved = True
            logger.info(
                "Preserving worktree at %s (has uncommitted changes)",
                self.worktree_path,
            )
            return

        self.worktree_preserved = False
        removed_by_git = False
        try:
            result = subprocess.run(
                ["git", "worktree", "remove", "--force", self.worktree_path],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                removed_by_git = True
                logger.info("Removed worktree at %s", self.worktree_path)
            else:
                logger.warning(
                    "git worktree remove exited %d for %s: %s",
                    result.returncode,
                    self.worktree_path,
                    (result.stderr or result.stdout or "").strip(),
                )
        except Exception as exc:
            logger.warning(
                "git worktree remove failed for %s: %s",
                self.worktree_path,
                exc,
            )

        # Fallback: if git didn't clean it up, force-remove the directory.
        if not removed_by_git and Path(self.worktree_path).exists():
            try:
                shutil.rmtree(self.worktree_path, ignore_errors=False)
                logger.info(
                    "Fallback rmtree removed worktree dir %s",
                    self.worktree_path,
                )
            except Exception as exc:
                logger.error(
                    "Failed to rmtree worktree dir %s: %s",
                    self.worktree_path,
                    exc,
                )

        if self.worktree_branch:
            try:
                result = subprocess.run(
                    ["git", "branch", "-D", self.worktree_branch],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    logger.warning(
                        "git branch -D %s exited %d: %s",
                        self.worktree_branch,
                        result.returncode,
                        (result.stderr or result.stdout or "").strip(),
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to delete worktree branch %s: %s",
                    self.worktree_branch,
                    exc,
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
                wt_path = self._setup_worktree()
                # Record the parent cwd for restoration; use _parent_cwd
                # (set at __init__ time) to avoid saving another agent's
                # worktree as the "original" when tasks run concurrently.
                os.chdir(wt_path)
            except RuntimeError as exc:
                try:
                    os.chdir(self._parent_cwd)
                except OSError:
                    pass
                self.status = "failed"
                self.error = str(exc)
                return f"[error] {exc}"

        # Seed the conversation with the user prompt
        self.conversation.messages.append(Message(role="user", content=prompt))

        try:
            final_message = await agent_loop_sync(
                self.provider,
                self.conversation,
                self.tools,
                system_prompt=self.system_prompt,
                max_iterations=self.max_iterations,
            )
            self.result = final_message.content
            self.status = "completed"

            # Process any queued messages that arrived while running
            while self._queued_messages:
                queued = self._queued_messages.pop(0)
                self.status = "running"
                self.conversation.messages.append(Message(role="user", content=queued))
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
                try:
                    os.chdir(self._parent_cwd)
                except OSError:
                    pass
                self._cleanup_worktree(force=(self.status == "failed"))
            self._fire_completion_callbacks()

    async def run_in_background(self, prompt: str) -> asyncio.Task[str]:
        """Run asynchronously. Returns an asyncio.Task the caller can await."""
        self._task = asyncio.create_task(self.run(prompt), name=f"subagent-{self.name}")
        return self._task

    # ------------------------------------------------------------------ #
    #  Introspection
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict[str, Any]:
        """Serialise agent state for status reporting."""
        d: dict[str, Any] = {
            "name": self.name,
            "agent_id": self.agent_id,
            "status": self.status,
            "isolation": self.isolation,
            "worktree_path": self.worktree_path,
            "worktree_branch": self.worktree_branch,
            "worktree_preserved": self.worktree_preserved,
            "result_preview": self.result[:200] if self.result else None,
            "error": self.error or None,
        }
        return d


class SubAgentManager:
    """Registry of spawned subagents.

    Provides spawn, get, and listing functionality so the parent agent
    can track and query its children.
    """

    def __init__(self) -> None:
        self.agents: dict[str, SubAgent] = {}
        self._notifications: list[dict[str, Any]] = []

    def spawn(
        self,
        name: str,
        provider: BaseProvider,
        tools: list[BaseTool],
        system_prompt: str,
        isolation: str = "none",
        max_iterations: int = 25,
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
            max_iterations=max_iterations,
        )
        agent.on_complete(self._on_agent_complete)
        self.agents[name] = agent
        return agent

    def _on_agent_complete(self, agent: SubAgent) -> None:
        """Record a notification when a managed agent completes."""
        notification: dict[str, Any] = {
            "agent_name": agent.name,
            "agent_id": agent.agent_id,
            "status": agent.status,
            "summary": agent.result if agent.status == "completed" else agent.error,
        }
        if agent.worktree_path:
            notification["worktree_path"] = agent.worktree_path
        if agent.worktree_branch:
            notification["worktree_branch"] = agent.worktree_branch
        self._notifications.append(notification)

    def drain_notifications(self) -> list[dict[str, Any]]:
        """Return and clear all pending completion notifications."""
        notifications = list(self._notifications)
        self._notifications.clear()
        return notifications

    @staticmethod
    def format_notification(notification: dict[str, Any]) -> str:
        """Format a notification dict as an XML-like string."""
        agent_id = notification.get("agent_id", "unknown")
        agent_name = notification.get("agent_name", "unknown")
        status = notification.get("status", "unknown")
        summary_text = notification.get("summary", "")

        parts = [
            "<task-notification>",
            f"<task-id>{agent_id}</task-id>",
            f"<summary>{agent_name} {status}</summary>",
            f"<event>{summary_text}</event>",
        ]

        wt_path = notification.get("worktree_path")
        wt_branch = notification.get("worktree_branch")
        if wt_path or wt_branch:
            attrs = []
            if wt_path:
                attrs.append(f'path="{wt_path}"')
            if wt_branch:
                attrs.append(f'branch="{wt_branch}"')
            parts.append(f"<worktree {' '.join(attrs)} />")

        parts.append("</task-notification>")
        return "\n".join(parts)

    def get(self, name: str) -> SubAgent | None:
        """Look up a subagent by name."""
        return self.agents.get(name)

    def get_by_id(self, agent_id: str) -> SubAgent | None:
        """Look up a subagent by its unique ID."""
        for agent in self.agents.values():
            if agent.agent_id == agent_id:
                return agent
        return None

    def _resolve_agent(self, name_or_id: str) -> SubAgent | None:
        """Resolve an agent by name or ID."""
        agent = self.get(name_or_id)
        if agent is not None:
            return agent
        return self.get_by_id(name_or_id)

    async def send_message(self, name_or_id: str, message: str) -> str:
        """Send a follow-up message to an agent by name or ID.

        If the agent is completed or failed, re-runs it with the new
        message.  If running, queues the message.  Returns ``[error]``
        if the agent doesn't exist.
        """
        agent = self._resolve_agent(name_or_id)
        if agent is None:
            return f"[error] No agent found with name or id {name_or_id!r}"
        return await agent.continue_with(message)

    def get_result(self, name_or_id: str) -> dict[str, Any] | None:
        """Return the result dict for an agent, or ``None``."""
        agent = self._resolve_agent(name_or_id)
        if agent is None:
            return None
        d: dict[str, Any] = {
            "name": agent.name,
            "agent_id": agent.agent_id,
            "status": agent.status,
            "result": agent.result,
            "error": agent.error or None,
        }
        if agent.worktree_path:
            d["worktree_path"] = agent.worktree_path
        if agent.worktree_branch:
            d["worktree_branch"] = agent.worktree_branch
        return d

    def list_active(self) -> list[SubAgent]:
        """Return all agents that are pending or running."""
        return [a for a in self.agents.values() if a.status in ("pending", "running")]

    def list_all(self) -> list[SubAgent]:
        """Return all agents regardless of status."""
        return list(self.agents.values())
