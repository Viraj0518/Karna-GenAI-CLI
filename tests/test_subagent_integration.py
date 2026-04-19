"""Integration tests for E4/E5 subagent enhancements.

Covers:
- Completion notification injection
- SendMessage to completed agent
- Foreground vs background execution
- Worktree auto-cleanup (no changes)
- Worktree preservation (with changes)
- Concurrent worktrees
- Error cleanup
- Result persistence
- Message queuing
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from karna.agents.subagent import SubAgent, SubAgentManager, _has_worktree_changes
from karna.config import KarnaConfig
from karna.models import Message, ModelInfo, StreamEvent
from karna.providers.base import BaseProvider
from karna.tools.base import BaseTool
from karna.tools.task import TaskTool, get_subagent_manager


# --------------------------------------------------------------------------- #
#  Mock provider
# --------------------------------------------------------------------------- #


class _DoneProvider(BaseProvider):
    """Provider that returns a canned final message."""

    name = "mock"

    def __init__(self, content: str = "done") -> None:
        super().__init__()
        self._content = content
        self.calls = 0

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Message:
        self.calls += 1
        return Message(role="assistant", content=self._content)

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type="text", text=self._content)
        yield StreamEvent(type="done")

    async def list_models(self) -> list[ModelInfo]:
        return []


class _SlowProvider(_DoneProvider):
    """Provider that delays before returning (for concurrency tests)."""

    def __init__(self, content: str = "done", delay: float = 0.1) -> None:
        super().__init__(content)
        self._delay = delay

    async def complete(self, messages, tools=None, **kw) -> Message:
        await asyncio.sleep(self._delay)
        return await super().complete(messages, tools, **kw)


class _BoomProvider(_DoneProvider):
    """Provider that raises an exception."""

    async def complete(self, messages, tools=None, **kw) -> Message:
        raise RuntimeError("kaboom")


class _EchoTool(BaseTool):
    name = "echo"
    description = "Echo input"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"input": {"type": "string"}},
        "required": ["input"],
    }

    async def execute(self, **kwargs: Any) -> str:
        return f"echo:{kwargs.get('input', '')}"


# --------------------------------------------------------------------------- #
#  Git helpers
# --------------------------------------------------------------------------- #


def _make_git_repo(path: Path) -> None:
    """Initialise a minimal git repo with one commit at *path*."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "commit.gpgsign", "false"], check=True)
    (path / "seed.txt").write_text("seed")
    subprocess.run(["git", "-C", str(path), "add", "seed.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-q", "-m", "init"],
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )


def _cleanup_agent_worktree(agent: SubAgent, repo: Path) -> None:
    """Best-effort cleanup of a preserved worktree for test teardown."""
    if agent.worktree_path and Path(agent.worktree_path).exists():
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", agent.worktree_path],
            check=False,
            capture_output=True,
        )
        if Path(agent.worktree_path).exists():
            shutil.rmtree(agent.worktree_path, ignore_errors=True)
    if agent.worktree_branch:
        subprocess.run(
            ["git", "-C", str(repo), "branch", "-D", agent.worktree_branch],
            check=False,
            capture_output=True,
        )


# =========================================================================== #
#  1. Completion notification injection (E4)
# =========================================================================== #


@pytest.mark.asyncio
async def test_completion_callback_fires_on_success(tmp_path, monkeypatch):
    """on_complete callback fires when agent completes successfully."""
    monkeypatch.chdir(tmp_path)
    provider = _DoneProvider(content="result text")

    agent = SubAgent(
        name="test-cb",
        provider=provider,
        tools=[],
        system_prompt="test",
    )

    received: list[SubAgent] = []
    agent.on_complete(lambda a: received.append(a))

    await agent.run("do something")

    assert len(received) == 1
    assert received[0].name == "test-cb"
    assert received[0].status == "completed"
    assert received[0].result == "result text"


@pytest.mark.asyncio
async def test_completion_callback_fires_on_failure(tmp_path, monkeypatch):
    """on_complete callback fires when agent fails."""
    monkeypatch.chdir(tmp_path)
    provider = _BoomProvider()

    agent = SubAgent(
        name="test-fail-cb",
        provider=provider,
        tools=[],
        system_prompt="test",
    )

    received: list[SubAgent] = []
    agent.on_complete(lambda a: received.append(a))

    await agent.run("do something")

    assert len(received) == 1
    assert received[0].status == "failed"
    assert "kaboom" in received[0].error


@pytest.mark.asyncio
async def test_manager_drains_notifications(tmp_path, monkeypatch):
    """SubAgentManager collects and drains completion notifications."""
    monkeypatch.chdir(tmp_path)
    manager = SubAgentManager()

    agent = manager.spawn(
        name="notifier",
        provider=_DoneProvider(content="task done"),
        tools=[],
        system_prompt="test",
    )

    await agent.run("go")

    notifications = manager.drain_notifications()
    assert len(notifications) == 1
    assert notifications[0]["agent_name"] == "notifier"
    assert notifications[0]["status"] == "completed"
    assert "task done" in notifications[0]["summary"]

    # Drain again should be empty
    assert manager.drain_notifications() == []


@pytest.mark.asyncio
async def test_notification_format(tmp_path, monkeypatch):
    """format_notification produces the expected XML-like string."""
    monkeypatch.chdir(tmp_path)
    manager = SubAgentManager()

    agent = manager.spawn(
        name="fmt-test",
        provider=_DoneProvider(content="all done"),
        tools=[],
        system_prompt="test",
    )
    await agent.run("go")

    notifications = manager.drain_notifications()
    formatted = manager.format_notification(notifications[0])

    assert "<task-notification>" in formatted
    assert f"<task-id>{agent.agent_id}</task-id>" in formatted
    assert "<summary>fmt-test completed</summary>" in formatted
    assert "<event>all done</event>" in formatted
    assert "</task-notification>" in formatted


# =========================================================================== #
#  2. SendMessage to completed agent (E4)
# =========================================================================== #


@pytest.mark.asyncio
async def test_send_message_to_completed_agent(tmp_path, monkeypatch):
    """Sending a message to a completed agent restarts its loop."""
    monkeypatch.chdir(tmp_path)

    call_count = 0

    class _CountingProvider(_DoneProvider):
        async def complete(self, messages, tools=None, **kw):
            nonlocal call_count
            call_count += 1
            return Message(role="assistant", content=f"response-{call_count}")

    manager = SubAgentManager()
    agent = manager.spawn(
        name="sendmsg",
        provider=_CountingProvider(),
        tools=[],
        system_prompt="test",
    )

    await agent.run("initial task")
    assert agent.status == "completed"
    assert agent.result == "response-1"

    # Send follow-up message
    result = await manager.send_message("sendmsg", "do more")
    assert result == "response-2"
    assert agent.status == "completed"
    assert agent.result == "response-2"

    # Also works by agent_id
    result = await manager.send_message(agent.agent_id, "even more")
    assert result == "response-3"


@pytest.mark.asyncio
async def test_send_message_to_running_agent_queues(tmp_path, monkeypatch):
    """Sending a message to a running agent queues it for later."""
    monkeypatch.chdir(tmp_path)

    call_count = 0

    class _SlowCountingProvider(_DoneProvider):
        async def complete(self, messages, tools=None, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                await asyncio.sleep(0.2)
            return Message(role="assistant", content=f"response-{call_count}")

    manager = SubAgentManager()
    agent = manager.spawn(
        name="queued",
        provider=_SlowCountingProvider(),
        tools=[],
        system_prompt="test",
    )

    task = await agent.run_in_background("initial task")

    # Send message while agent is running (should queue)
    await asyncio.sleep(0.05)
    result = await agent.continue_with("queued message")
    assert "[queued]" in result

    # Wait for completion
    await task

    assert agent.status == "completed"
    # The queued message should have been processed
    assert call_count >= 2


@pytest.mark.asyncio
async def test_send_message_to_nonexistent_agent(tmp_path, monkeypatch):
    """Sending to a nonexistent agent returns error."""
    monkeypatch.chdir(tmp_path)
    manager = SubAgentManager()

    result = await manager.send_message("ghost", "hello")
    assert "[error]" in result
    assert "ghost" in result


@pytest.mark.asyncio
async def test_send_message_to_failed_agent_restarts(tmp_path, monkeypatch):
    """Sending to a failed agent restarts it (with new provider response)."""
    monkeypatch.chdir(tmp_path)

    first_call = True

    class _FailThenSucceed(_DoneProvider):
        async def complete(self, messages, tools=None, **kw):
            nonlocal first_call
            if first_call:
                first_call = False
                raise RuntimeError("temporary failure")
            return Message(role="assistant", content="recovered")

    manager = SubAgentManager()
    agent = manager.spawn(
        name="recover",
        provider=_FailThenSucceed(),
        tools=[],
        system_prompt="test",
    )

    await agent.run("initial")
    assert agent.status == "failed"

    result = await manager.send_message("recover", "try again")
    assert result == "recovered"
    assert agent.status == "completed"


# =========================================================================== #
#  3. Foreground vs background execution (E4)
# =========================================================================== #


@pytest.mark.asyncio
async def test_task_tool_foreground_awaits_result(tmp_path, monkeypatch):
    """Foreground mode: TaskTool awaits the subagent result."""
    monkeypatch.chdir(tmp_path)
    provider = _DoneProvider(content="foreground result")

    tool = TaskTool(provider=provider, parent_config=KarnaConfig(), tools=[])

    result = await tool.execute(
        description="fg-task",
        prompt="do thing",
        run_in_background=False,
        isolation="none",
    )

    assert result == "foreground result"


@pytest.mark.asyncio
async def test_task_tool_background_returns_agent_id(tmp_path, monkeypatch):
    """Background mode: TaskTool returns agent ID immediately."""
    monkeypatch.chdir(tmp_path)
    provider = _SlowProvider(content="bg result", delay=0.2)

    tool = TaskTool(provider=provider, parent_config=KarnaConfig(), tools=[])

    result = await tool.execute(
        description="bg-task",
        prompt="do thing",
        run_in_background=True,
        isolation="none",
    )

    parsed = json.loads(result)
    assert parsed["status"] == "started"
    assert "agent_id" in parsed

    # Wait a bit for background task to complete
    await asyncio.sleep(0.5)

    manager = get_subagent_manager()
    agent = manager._resolve_agent(parsed["agent_id"])
    assert agent is not None
    assert agent.status == "completed"


@pytest.mark.asyncio
async def test_task_tool_default_is_foreground(tmp_path, monkeypatch):
    """Default run_in_background=False (foreground) behavior."""
    monkeypatch.chdir(tmp_path)
    provider = _DoneProvider(content="default fg result")

    tool = TaskTool(provider=provider, parent_config=KarnaConfig(), tools=[])

    result = await tool.execute(
        description="default-fg",
        prompt="test",
        isolation="none",
    )

    # Default is foreground -- should be the raw result, not JSON
    assert result == "default fg result"


# =========================================================================== #
#  4. Worktree auto-cleanup: no changes (E5)
# =========================================================================== #


@pytest.mark.asyncio
async def test_worktree_auto_cleanup_no_changes(tmp_path, monkeypatch):
    """Worktree is auto-removed when subagent makes no file changes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_git_repo(repo)
    monkeypatch.chdir(repo)

    provider = _DoneProvider(content="no changes made")
    agent = SubAgent(
        name="clean-agent",
        provider=provider,
        tools=[],
        system_prompt="test",
        isolation="worktree",
    )

    await agent.run("read only task")

    assert agent.status == "completed"
    assert not agent.worktree_preserved
    # Worktree should have been cleaned up
    if agent.worktree_path:
        assert not Path(agent.worktree_path).exists()


# =========================================================================== #
#  5. Worktree preservation: with changes (E5)
# =========================================================================== #


@pytest.mark.asyncio
async def test_worktree_preserved_with_changes(tmp_path, monkeypatch):
    """Worktree is preserved when subagent makes file changes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_git_repo(repo)
    monkeypatch.chdir(repo)

    class _FileWriter(_DoneProvider):
        async def complete(self, messages, tools=None, **kw):
            # Write a file in cwd (the worktree)
            Path("new_file.txt").write_text("subagent was here")
            return Message(role="assistant", content="wrote file")

    provider = _FileWriter()
    agent = SubAgent(
        name="writer-agent",
        provider=provider,
        tools=[],
        system_prompt="test",
        isolation="worktree",
    )

    await agent.run("write a file")

    assert agent.status == "completed"
    assert agent.worktree_preserved
    assert agent.worktree_path is not None
    assert Path(agent.worktree_path).exists()
    assert agent.worktree_branch is not None
    assert "writer-agent" in agent.worktree_branch

    # to_dict should include worktree info
    d = agent.to_dict()
    assert d.get("worktree_preserved") is True
    assert "worktree_branch" in d

    # Clean up manually
    _cleanup_agent_worktree(agent, repo)


# =========================================================================== #
#  6. Concurrent worktrees (E5)
# =========================================================================== #


@pytest.mark.asyncio
async def test_concurrent_worktrees_unique_branches(tmp_path, monkeypatch):
    """Multiple concurrent subagents get unique worktree paths and branches."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_git_repo(repo)
    monkeypatch.chdir(repo)

    agents = []
    for i in range(3):
        agent = SubAgent(
            name="concurrent",
            provider=_SlowProvider(content=f"result-{i}", delay=0.1),
            tools=[],
            system_prompt="test",
            isolation="worktree",
        )
        agents.append(agent)

    tasks = [asyncio.create_task(a.run(f"task {i}")) for i, a in enumerate(agents)]
    results = await asyncio.gather(*tasks)

    # All should complete
    assert all(r.startswith("result-") for r in results)

    # All should have unique worktree paths
    wt_paths = [a.worktree_path for a in agents if a.worktree_path]
    assert len(wt_paths) == len(set(wt_paths)), f"Worktree paths not unique: {wt_paths}"

    # All should have unique branch names
    branches = [a.worktree_branch for a in agents if a.worktree_branch]
    assert len(branches) == len(set(branches)), f"Branches not unique: {branches}"


# =========================================================================== #
#  7. Error cleanup (E5)
# =========================================================================== #


@pytest.mark.asyncio
async def test_worktree_cleanup_on_error(tmp_path, monkeypatch):
    """Worktree is force-cleaned on subagent failure."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_git_repo(repo)
    monkeypatch.chdir(repo)

    class _ErrorFileWriter(_DoneProvider):
        async def complete(self, messages, tools=None, **kw):
            # Write file then crash
            Path("dirty.txt").write_text("dirty")
            raise RuntimeError("crash after write")

    provider = _ErrorFileWriter()
    agent = SubAgent(
        name="crash-agent",
        provider=provider,
        tools=[],
        system_prompt="test",
        isolation="worktree",
    )

    result = await agent.run("crash")

    assert agent.status == "failed"
    assert "[error]" in result
    # Even though there were changes, force=True on failure means cleanup
    assert not agent.worktree_preserved
    if agent.worktree_path:
        assert not Path(agent.worktree_path).exists()

    # cwd should be restored
    assert Path(os.getcwd()).resolve() == repo.resolve()


@pytest.mark.asyncio
async def test_worktree_cleanup_on_setup_failure(tmp_path, monkeypatch):
    """Agent handles worktree setup failure gracefully."""
    # Non-git directory
    monkeypatch.chdir(tmp_path)

    agent = SubAgent(
        name="no-git",
        provider=_DoneProvider(),
        tools=[],
        system_prompt="test",
        isolation="worktree",
    )

    result = await agent.run("go")

    assert agent.status == "failed"
    assert "[error]" in result


# =========================================================================== #
#  8. Result persistence (E4)
# =========================================================================== #


@pytest.mark.asyncio
async def test_result_persistence_survives_retrieval(tmp_path, monkeypatch):
    """Results can be retrieved by name or ID after completion."""
    monkeypatch.chdir(tmp_path)
    manager = SubAgentManager()

    agent = manager.spawn(
        name="persist-test",
        provider=_DoneProvider(content="important result"),
        tools=[],
        system_prompt="test",
    )

    await agent.run("go")

    # Retrieve by name
    result = manager.get_result("persist-test")
    assert result is not None
    assert result["result"] == "important result"
    assert result["status"] == "completed"

    # Retrieve by ID
    result_by_id = manager.get_result(agent.agent_id)
    assert result_by_id is not None
    assert result_by_id["result"] == "important result"


@pytest.mark.asyncio
async def test_result_persistence_nonexistent(tmp_path, monkeypatch):
    """get_result for nonexistent agent returns None."""
    monkeypatch.chdir(tmp_path)
    manager = SubAgentManager()
    assert manager.get_result("nope") is None


@pytest.mark.asyncio
async def test_result_persistence_includes_worktree_info(tmp_path, monkeypatch):
    """Persisted result includes worktree info when preserved."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_git_repo(repo)
    monkeypatch.chdir(repo)

    class _FileWriter(_DoneProvider):
        async def complete(self, messages, tools=None, **kw):
            Path("output.txt").write_text("data")
            return Message(role="assistant", content="wrote output")

    manager = SubAgentManager()
    agent = manager.spawn(
        name="persist-wt",
        provider=_FileWriter(),
        tools=[],
        system_prompt="test",
        isolation="worktree",
    )

    await agent.run("write")

    result = manager.get_result("persist-wt")
    assert result is not None
    assert "worktree_path" in result
    assert "worktree_branch" in result

    # Clean up
    _cleanup_agent_worktree(agent, repo)


# =========================================================================== #
#  9. TaskTool action routing (E4)
# =========================================================================== #


@pytest.mark.asyncio
async def test_task_tool_foreground_create(tmp_path, monkeypatch):
    """TaskTool create action with foreground mode."""
    monkeypatch.chdir(tmp_path)

    call_count = 0

    class _CountProvider(_DoneProvider):
        async def complete(self, messages, tools=None, **kw):
            nonlocal call_count
            call_count += 1
            return Message(role="assistant", content=f"resp-{call_count}")

    provider = _CountProvider()
    tool = TaskTool(provider=provider, parent_config=KarnaConfig(), tools=[])

    # Foreground agent
    result1 = await tool.execute(
        description="msg-target",
        prompt="initial",
        run_in_background=False,
        isolation="none",
    )
    assert result1 == "resp-1"


@pytest.mark.asyncio
async def test_task_tool_stop_action(tmp_path, monkeypatch):
    """TaskTool stop action returns error for nonexistent agent."""
    monkeypatch.chdir(tmp_path)
    tool = TaskTool(provider=_DoneProvider(), parent_config=KarnaConfig(), tools=[])

    result = await tool.execute(
        description="stop",
        prompt="unused",
        action="stop",
        agent_id="nonexistent",
    )

    assert "[error]" in result


@pytest.mark.asyncio
async def test_task_tool_send_message_missing_fields(tmp_path, monkeypatch):
    """TaskTool send_message requires agent_id and message."""
    monkeypatch.chdir(tmp_path)
    tool = TaskTool(provider=_DoneProvider(), parent_config=KarnaConfig(), tools=[])

    # Missing agent_id
    result = await tool.execute(
        description="x",
        prompt="x",
        action="send_message",
    )
    assert "[error]" in result
    assert "agent_id" in result

    # Missing message
    result = await tool.execute(
        description="x",
        prompt="x",
        action="send_message",
        agent_id="some-id",
    )
    assert "[error]" in result
    assert "message" in result


# =========================================================================== #
#  10. _has_worktree_changes helper (E5)
# =========================================================================== #


def test_has_worktree_changes_clean(tmp_path):
    """_has_worktree_changes returns False for a clean repo."""
    _make_git_repo(tmp_path)
    assert not _has_worktree_changes(tmp_path)


def test_has_worktree_changes_dirty(tmp_path):
    """_has_worktree_changes returns True when there are uncommitted files."""
    _make_git_repo(tmp_path)
    (tmp_path / "new_file.txt").write_text("dirty")
    assert _has_worktree_changes(tmp_path)


def test_has_worktree_changes_not_git(tmp_path):
    """_has_worktree_changes returns False for non-git directory."""
    assert not _has_worktree_changes(tmp_path)


# =========================================================================== #
#  11. Agent ID assignment (E4)
# =========================================================================== #


def test_agent_has_unique_id():
    """Each SubAgent gets a unique agent_id."""
    agent1 = SubAgent(
        name="a",
        provider=_DoneProvider(),
        tools=[],
        system_prompt="test",
    )
    agent2 = SubAgent(
        name="a",
        provider=_DoneProvider(),
        tools=[],
        system_prompt="test",
    )
    assert agent1.agent_id != agent2.agent_id
    assert len(agent1.agent_id) == 12


# =========================================================================== #
#  12. Manager ID-based lookup (E4)
# =========================================================================== #


@pytest.mark.asyncio
async def test_manager_lookup_by_id(tmp_path, monkeypatch):
    """Manager can look up agents by ID."""
    monkeypatch.chdir(tmp_path)
    manager = SubAgentManager()

    agent = manager.spawn(
        name="lookup",
        provider=_DoneProvider(),
        tools=[],
        system_prompt="test",
    )

    assert manager.get_by_id(agent.agent_id) is agent
    assert manager._resolve_agent(agent.agent_id) is agent
    assert manager._resolve_agent("lookup") is agent
    assert manager._resolve_agent("nonexistent") is None


# =========================================================================== #
#  13. Notification includes worktree info (E5)
# =========================================================================== #


@pytest.mark.asyncio
async def test_notification_includes_worktree_when_preserved(tmp_path, monkeypatch):
    """Completion notification includes worktree info when changes exist."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_git_repo(repo)
    monkeypatch.chdir(repo)

    class _FileWriter(_DoneProvider):
        async def complete(self, messages, tools=None, **kw):
            Path("output.txt").write_text("data")
            return Message(role="assistant", content="wrote it")

    manager = SubAgentManager()
    agent = manager.spawn(
        name="wt-notify",
        provider=_FileWriter(),
        tools=[],
        system_prompt="test",
        isolation="worktree",
    )

    await agent.run("write file")

    notifications = manager.drain_notifications()
    assert len(notifications) == 1
    n = notifications[0]
    assert "worktree_path" in n
    assert "worktree_branch" in n

    # Format should include worktree element
    formatted = manager.format_notification(n)
    assert "<worktree" in formatted

    # Clean up
    _cleanup_agent_worktree(agent, repo)
