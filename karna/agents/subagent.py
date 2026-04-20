"""Subagent system — independent agents with their own conversation context.

A SubAgent is a lightweight agent that runs in the background with its own
conversation history, tools, and optional git worktree isolation. The
SubAgentManager tracks all spawned agents and provides lookup / listing.

The canonical entrypoint for one-shot subagent runs is :func:`spawn_subagent`,
which returns the final assistant content as a string. The legacy
``SubAgent`` / ``SubAgentManager`` classes are retained for long-running
background agents tracked by name.

Enhanced (E4/E5) with:
- Completion callbacks (``on_complete``) for parent notification
- SendMessage to continue completed/running agents
- Foreground vs background execution
- Worktree auto-cleanup (no changes) vs preservation (with changes)
- Result persistence for context-compaction survival
- Message queuing for in-flight agents

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
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Literal
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


def _has_worktree_changes(worktree_path: Path) -> bool:
    """Return True if the worktree has uncommitted or untracked files.

    Uses ``git status --porcelain`` in the worktree directory.  Returns
    False if the check fails (so the caller errs on the side of cleanup).
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree_path), "status", "--porcelain"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        return bool(result.stdout.strip())
    except (FileNotFoundError, OSError):
        return False


def _save_tools_cwd(tools: list[BaseTool]) -> dict[int, str]:
    """Snapshot the current ``_cwd`` of every tool that tracks one.

    Returns a mapping from ``id(tool)`` to its saved cwd.  Used to
    restore per-tool state after a subagent finishes — some tools
    (e.g. BashTool after ``cd /some/path``) carry a custom cwd that
    must not be clobbered.
    """
    saved: dict[int, str] = {}
    for tool in tools:
        if hasattr(tool, "_cwd"):
            saved[id(tool)] = tool._cwd
    return saved


def _restore_tools_cwd(tools: list[BaseTool], saved: dict[int, str]) -> None:
    """Restore each tool's ``_cwd`` from a snapshot produced by :func:`_save_tools_cwd`."""
    for tool in tools:
        tid = id(tool)
        if tid in saved:
            tool._cwd = saved[tid]


def _set_tools_cwd(tools: list[BaseTool], cwd: str) -> None:
    """Set the working directory on all tools that track one.

    Used instead of ``os.chdir()`` so concurrent subagents don't
    stomp on each other's global process cwd.  Only tools that
    expose a ``_cwd`` attribute (e.g. BashTool, GitOpsTool) are
    affected.
    """
    for tool in tools:
        if hasattr(tool, "_cwd"):
            tool._cwd = cwd


@asynccontextmanager
async def _isolation_context(
    isolation: Literal["none", "worktree"],
    worktree_base: Path | None,
    tools: list[BaseTool] | None = None,
) -> AsyncIterator[Path]:
    """Yield the cwd the subagent should run in.

    For ``isolation="none"`` this is simply the current cwd.

    For ``isolation="worktree"`` a fresh worktree is created under
    ``worktree_base`` (defaulting to the system temp dir). The worktree
    is removed on exit, even if the body raises. If the current cwd is
    not a git repo, we log a warning and fall back to no isolation
    instead of crashing.

    **Concurrency safety**: this context manager does NOT call
    ``os.chdir()`` — it updates tool ``_cwd`` attributes instead so
    concurrent subagents don't stomp on each other's working directory.
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

    # Save each tool's original _cwd so we can restore it exactly,
    # rather than blindly resetting to os.getcwd().
    saved_cwds: dict[int, str] = {}
    try:
        # Point tools at the worktree instead of mutating process-global cwd.
        if tools:
            saved_cwds = _save_tools_cwd(tools)
            _set_tools_cwd(tools, str(worktree_path))
        yield worktree_path
    finally:
        # Restore each tool to its original per-tool cwd.
        if tools:
            _restore_tools_cwd(tools, saved_cwds)
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
        async with _isolation_context(isolation, worktree_base, tools=tools):
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
#  Long-running subagent with lifecycle management (E4/E5)
# --------------------------------------------------------------------------- #


class SubAgent:
    """Independent agent with its own conversation context.

    Each subagent has:
    - A unique name (used as key in the manager)
    - A unique agent_id for programmatic lookup (E4)
    - Its own Conversation (message history)
    - A set of tools it can use
    - A system prompt
    - An optional worktree for filesystem isolation
    - Completion callbacks for parent notification (E4)
    - A message queue for SendMessage while running (E4)
    - Worktree auto-cleanup / preservation logic (E5)
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
        self.agent_id: str = uuid.uuid4().hex[:12]
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
        # Whether worktree had changes and was preserved (not cleaned up)
        self.worktree_preserved: bool = False

        # E4: Completion callbacks
        self._on_complete_callbacks: list[Callable[[SubAgent], Any]] = []

        # E4: Message queue -- messages sent while agent is running
        self._message_queue: deque[str] = deque()

        # E4: Parent cwd preserved for worktree operations
        self._parent_cwd: str | None = None

    # ------------------------------------------------------------------ #
    #  Callback registration (E4)
    # ------------------------------------------------------------------ #

    def on_complete(self, callback: Callable[[SubAgent], Any]) -> None:
        """Register a callback invoked when this agent completes or fails.

        The callback receives the SubAgent instance. If async, it is
        scheduled as a task. If sync, it is called directly.
        """
        self._on_complete_callbacks.append(callback)

    def _fire_callbacks(self) -> None:
        """Invoke all registered on_complete callbacks."""
        for cb in self._on_complete_callbacks:
            try:
                ret = cb(self)
                if asyncio.iscoroutine(ret):
                    asyncio.ensure_future(ret)
            except Exception as exc:
                logger.warning(
                    "on_complete callback for subagent %s raised: %s",
                    self.name,
                    exc,
                )

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

    def _cleanup_worktree(self, *, force: bool = False) -> None:
        """Remove the git worktree and its branch.

        If the worktree has uncommitted changes and *force* is False, the
        worktree is preserved and ``self.worktree_preserved`` is set to True
        so the caller can report the branch/path back (E5).

        When *force* is True (e.g. on failure/crash), always clean up
        regardless of changes.

        Failures are logged explicitly -- never silently ignored.  Tries
        ``git worktree remove`` first, then falls back to
        ``shutil.rmtree`` if git leaves the directory behind.
        """
        if not self.worktree_path:
            return

        # E5: Auto-cleanup vs preservation
        wt = Path(self.worktree_path)
        if not force and wt.exists() and _has_worktree_changes(wt):
            self.worktree_preserved = True
            logger.info(
                "Worktree %s has changes -- preserving (branch %s)",
                self.worktree_path,
                self.worktree_branch,
            )
            return

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

    async def run(self, prompt: str, *, max_iterations: int = 25) -> str:
        """Run the subagent to completion. Returns final response text.

        Sets up worktree if ``isolation='worktree'``, runs the agent loop,
        and cleans up on completion (or preserves worktree if changes exist).

        **Concurrency safety**: does NOT call ``os.chdir()`` — instead
        uses ``_set_tools_cwd()`` to point tool working directories at
        the worktree, so concurrent subagents don't stomp on each other.

        Callback firing order: tool cwd restore -> worktree cleanup -> callbacks.
        This ensures ``worktree_preserved`` is set before callbacks see it.
        """
        self.status = "running"
        self._parent_cwd = os.getcwd()

        # Set up worktree isolation if requested
        if self.isolation == "worktree":
            try:
                self._setup_worktree()
            except RuntimeError as exc:
                self.status = "failed"
                self.error = str(exc)
                self._fire_callbacks()
                return f"[error] {exc}"

        # Save each tool's original per-tool cwd before overwriting,
        # so we restore it correctly (not to a generic os.getcwd()).
        saved_cwds: dict[int, str] = _save_tools_cwd(self.tools)

        # Point tools at the worktree instead of mutating global cwd
        if self.worktree_path:
            _set_tools_cwd(self.tools, self.worktree_path)

        # Seed the conversation with the user prompt
        self.conversation.messages.append(Message(role="user", content=prompt))

        return_value: str = ""
        try:
            final_message = await agent_loop_sync(
                self.provider,
                self.conversation,
                self.tools,
                system_prompt=self.system_prompt,
                max_iterations=max_iterations,
            )
            self.result = final_message.content
            self.status = "completed"

            # E4: Process any queued messages (SendMessage while running)
            while self._message_queue:
                queued_msg = self._message_queue.popleft()
                self.conversation.messages.append(Message(role="user", content=queued_msg))
                final_message = await agent_loop_sync(
                    self.provider,
                    self.conversation,
                    self.tools,
                    system_prompt=self.system_prompt,
                    max_iterations=max_iterations,
                )
                self.result = final_message.content

            return_value = self.result
        except Exception as exc:
            self.status = "failed"
            self.error = str(exc)
            logger.exception("Subagent %s failed: %s", self.name, exc)
            return_value = f"[error] Subagent {self.name} failed: {exc}"
        finally:
            # Restore each tool to its original per-tool cwd
            _restore_tools_cwd(self.tools, saved_cwds)
            if self.isolation == "worktree":
                # E5: force cleanup on failure, conditional on success
                self._cleanup_worktree(force=(self.status == "failed"))
            # Fire callbacks AFTER cleanup so worktree_preserved is set
            self._fire_callbacks()

        return return_value

    async def continue_with(self, message: str) -> str:
        """Continue a completed agent with a new user message (SendMessage, E4).

        Appends the message to the agent's conversation and runs another
        agent loop iteration. Returns the new final response.

        If the agent is currently running, queues the message for processing
        after the current loop finishes.

        If the agent was created with ``isolation="worktree"`` and the
        worktree still exists, the follow-up loop runs with tool cwds
        pointed at the worktree so file operations happen in the right
        directory.
        """
        if self.status == "running":
            # Queue the message for when the current run finishes
            self._message_queue.append(message)
            return f"[queued] Message queued for subagent {self.name} (currently running)."

        if self.status not in ("completed", "failed"):
            return f"[error] Cannot send message to subagent {self.name} in state {self.status}."

        # Restart from completed/failed state
        self.status = "running"
        self.error = ""

        # Save per-tool cwds before overwriting
        saved_cwds = _save_tools_cwd(self.tools)

        # Re-enter worktree if one exists (e.g. preserved with changes)
        if self.worktree_path and Path(self.worktree_path).is_dir():
            _set_tools_cwd(self.tools, self.worktree_path)

        self.conversation.messages.append(Message(role="user", content=message))

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
            self._fire_callbacks()
            return self.result
        except Exception as exc:
            self.status = "failed"
            self.error = str(exc)
            logger.exception("Subagent %s continue_with failed: %s", self.name, exc)
            self._fire_callbacks()
            return f"[error] Subagent {self.name} failed: {exc}"
        finally:
            # Restore each tool to its original per-tool cwd
            _restore_tools_cwd(self.tools, saved_cwds)

    async def run_in_background(self, prompt: str, *, max_iterations: int = 25) -> asyncio.Task[str]:
        """Run asynchronously. Returns an asyncio.Task the caller can await."""
        self._task = asyncio.create_task(self.run(prompt, max_iterations=max_iterations), name=f"subagent-{self.name}")
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
            "result_preview": self.result[:200] if self.result else None,
            "error": self.error or None,
        }
        # E5: Include branch info if worktree was preserved
        if self.worktree_preserved and self.worktree_branch:
            d["worktree_branch"] = self.worktree_branch
            d["worktree_preserved"] = True
        return d


class SubAgentManager:
    """Registry of spawned subagents.

    Provides spawn, get, listing, send_message, and result retrieval
    functionality so the parent agent can track and query its children.

    E4 additions:
    - ``send_message`` to continue a completed/running agent
    - ``get_result`` for compaction-safe result retrieval
    - ``drain_notifications`` to collect completion events for parent injection
    - Persistent result storage (survives context compaction)
    """

    def __init__(self) -> None:
        self.agents: dict[str, SubAgent] = {}
        # agent_id -> SubAgent mapping for ID-based lookup
        self._agents_by_id: dict[str, SubAgent] = {}
        # E4: Persistent result storage (survives context compaction)
        self._results: dict[str, dict[str, Any]] = {}
        # E4: Pending notifications for parent injection
        self._pending_notifications: list[dict[str, str]] = []

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
        self._agents_by_id[agent.agent_id] = agent

        # E4: Auto-register completion callback for notifications + persistence
        agent.on_complete(self._on_agent_complete)

        return agent

    def _on_agent_complete(self, agent: SubAgent) -> None:
        """Internal callback: persist result and queue notification."""
        # Persist the result
        result_entry: dict[str, Any] = {
            "agent_id": agent.agent_id,
            "name": agent.name,
            "status": agent.status,
            "result": agent.result,
            "error": agent.error,
        }
        # E5: Include worktree info if preserved
        if agent.worktree_preserved and agent.worktree_path:
            result_entry["worktree_path"] = agent.worktree_path
            result_entry["worktree_branch"] = agent.worktree_branch
        self._results[agent.agent_id] = result_entry
        self._results[agent.name] = result_entry  # Also indexed by name

        # Queue notification for parent
        summary = agent.result[:500] if agent.result else agent.error[:500]
        notification: dict[str, str] = {
            "agent_id": agent.agent_id,
            "agent_name": agent.name,
            "status": agent.status,
            "summary": summary,
        }
        if agent.worktree_preserved:
            notification["worktree_path"] = agent.worktree_path or ""
            notification["worktree_branch"] = agent.worktree_branch or ""
        self._pending_notifications.append(notification)

    def get(self, name: str) -> SubAgent | None:
        """Look up a subagent by name."""
        return self.agents.get(name)

    def get_by_id(self, agent_id: str) -> SubAgent | None:
        """Look up a subagent by its unique agent_id."""
        return self._agents_by_id.get(agent_id)

    def _resolve_agent(self, agent_id_or_name: str) -> SubAgent | None:
        """Resolve an agent by ID or name."""
        agent = self._agents_by_id.get(agent_id_or_name)
        if agent is None:
            agent = self.agents.get(agent_id_or_name)
        return agent

    async def send_message(self, agent_id_or_name: str, message: str) -> str:
        """Send a message to a completed or running subagent (E4).

        If the agent is completed/failed, restarts its loop with the new
        message. If running, queues the message for processing after the
        current loop finishes.

        Returns the agent's response or a status string.
        """
        agent = self._resolve_agent(agent_id_or_name)
        if agent is None:
            return f"[error] No subagent found with id/name: {agent_id_or_name}"

        return await agent.continue_with(message)

    def get_result(self, agent_id_or_name: str) -> dict[str, Any] | None:
        """Retrieve a persisted subagent result (E4).

        Survives context compaction -- results are stored separately from
        the conversation history.
        """
        return self._results.get(agent_id_or_name)

    def drain_notifications(self) -> list[dict[str, str]]:
        """Return and clear all pending completion notifications (E4).

        Each notification is a dict with keys: agent_id, agent_name,
        status, summary. The parent agent loop should call this before
        each turn and inject matching system messages.
        """
        notifications = list(self._pending_notifications)
        self._pending_notifications.clear()
        return notifications

    def format_notification(self, notification: dict[str, str]) -> str:
        """Format a notification dict as a system message string (E4)."""

        def _esc(text: str) -> str:
            """Escape XML-special characters to prevent injection."""
            return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

        parts = [
            "<task-notification>",
            f"<task-id>{_esc(notification['agent_id'])}</task-id>",
            f"<summary>{_esc(notification['agent_name'])} {_esc(notification['status'])}</summary>",
            f"<event>{_esc(notification['summary'])}</event>",
        ]
        if notification.get("worktree_path"):
            wt_path = _esc(notification["worktree_path"])
            wt_branch = _esc(notification.get("worktree_branch", ""))
            parts.append(f'<worktree path="{wt_path}" branch="{wt_branch}"/>')
        parts.append("</task-notification>")
        return "\n".join(parts)

    def list_active(self) -> list[SubAgent]:
        """Return all agents that are pending or running."""
        return [a for a in self.agents.values() if a.status in ("pending", "running")]

    def list_all(self) -> list[SubAgent]:
        """Return all agents regardless of status."""
        return list(self.agents.values())
