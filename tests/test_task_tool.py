"""Tests for ``karna.tools.task.TaskTool`` — the in-process subagent spawn tool."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from karna.config import KarnaConfig
from karna.models import Message, ModelInfo, StreamEvent, ToolCall
from karna.providers.base import BaseProvider
from karna.tools.base import BaseTool
from karna.tools.task import TaskTool

# --------------------------------------------------------------------------- #
#  Mocks
# --------------------------------------------------------------------------- #


class _ScriptedProvider(BaseProvider):
    """Provider that returns a scripted sequence of messages."""

    name = "mock"

    def __init__(self, responses: list[Message]) -> None:
        super().__init__()
        self._responses = list(responses)
        self.call_count = 0

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Message:
        self.call_count += 1
        if not self._responses:
            return Message(role="assistant", content="(exhausted)")
        return self._responses.pop(0)

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        msg = await self.complete(messages, tools, system_prompt=system_prompt)
        if msg.content:
            yield StreamEvent(type="text", text=msg.content)
        yield StreamEvent(type="done")

    async def list_models(self) -> list[ModelInfo]:
        return []


class _EchoTool(BaseTool):
    """Trivial tool used to verify tools flow through to the subagent."""

    name = "echo"
    description = "Echo input"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"input": {"type": "string"}},
        "required": ["input"],
    }

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def execute(self, **kwargs: Any) -> str:
        self.calls += 1
        return f"echo:{kwargs.get('input', '')}"


def _make_git_repo(path: Path) -> None:
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


# --------------------------------------------------------------------------- #
#  Tests
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_task_tool_simple_prompt_returns_final_message(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = _ScriptedProvider([Message(role="assistant", content="the answer is 42")])

    tool = TaskTool(provider=provider, parent_config=KarnaConfig(), tools=[_EchoTool()])

    result = await tool.execute(
        description="compute",
        prompt="what is 6*7?",
        isolation="none",
    )

    assert result == "the answer is 42"
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_task_tool_respects_max_iterations(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    echo = _EchoTool()

    # Provider always issues a tool call, never terminates cleanly.
    responses = [
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id=f"tc_{i}", name="echo", arguments={"input": f"x{i}"})],
        )
        for i in range(20)
    ]
    provider = _ScriptedProvider(responses)
    tool = TaskTool(provider=provider, parent_config=KarnaConfig(), tools=[echo])

    result = await tool.execute(
        description="loop",
        prompt="keep calling echo",
        subagent_type="code",  # code includes all tools, ensures echo is exposed
        tools=["echo"],  # explicit override so echo flows through
        max_iterations=3,
    )

    # The loop should have capped executions; echo called at most max_iterations.
    assert echo.calls <= 3
    # Result message contains the "maximum iterations" error marker from loop.py
    assert "maximum iterations" in result.lower() or result == "(exhausted)"


@pytest.mark.asyncio
async def test_task_tool_worktree_isolation_creates_and_cleans(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_git_repo(repo)
    monkeypatch.chdir(repo)

    worktree_base = tmp_path / "wts"
    provider = _ScriptedProvider([Message(role="assistant", content="done in wt")])

    tool = TaskTool(
        provider=provider,
        parent_config=KarnaConfig(),
        tools=[],
        worktree_base=worktree_base,
    )

    result = await tool.execute(
        description="work in worktree",
        prompt="do the thing",
        isolation="worktree",
    )

    assert result == "done in wt"
    # cwd restored
    assert Path(os.getcwd()).resolve() == repo.resolve()
    # worktree cleaned up
    leftover = list(worktree_base.iterdir()) if worktree_base.exists() else []
    assert leftover == []


@pytest.mark.asyncio
async def test_task_tool_invalid_subagent_type_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = _ScriptedProvider([Message(role="assistant", content="x")])
    tool = TaskTool(provider=provider, parent_config=KarnaConfig(), tools=[])

    with pytest.raises(ValueError, match="Invalid subagent_type"):
        await tool.execute(
            description="bad",
            prompt="x",
            subagent_type="nonexistent_type",
        )


@pytest.mark.asyncio
async def test_task_tool_without_provider_returns_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tool = TaskTool()  # no provider

    result = await tool.execute(description="x", prompt="x")
    assert result.startswith("[error]")
    assert "provider" in result.lower()


@pytest.mark.asyncio
async def test_task_tool_default_filter_excludes_dangerous_tools(tmp_path, monkeypatch):
    """Default subagent_type='general' filters out bash/write/edit/git."""
    monkeypatch.chdir(tmp_path)

    # Capture which tools the provider saw in its `tools` arg.
    captured: dict[str, Any] = {}

    class _Peek(_ScriptedProvider):
        async def complete(self, messages, tools=None, **kw):
            captured["tools"] = tools or []
            return await super().complete(messages, tools, **kw)

    provider = _Peek([Message(role="assistant", content="done")])

    # Build a mix including a "dangerous" tool name and a safe one.
    class _FakeBash(BaseTool):
        name = "bash"
        description = "d"
        parameters: dict[str, Any] = {"type": "object", "properties": {}}

        async def execute(self, **kwargs: Any) -> str:
            return ""

    tool = TaskTool(
        provider=provider,
        parent_config=KarnaConfig(),
        tools=[_EchoTool(), _FakeBash()],
    )

    await tool.execute(description="d", prompt="p")  # defaults to subagent_type=general

    tool_names = [t.get("function", {}).get("name") for t in captured["tools"]]
    assert "echo" in tool_names
    assert "bash" not in tool_names, "general subagent_type should exclude bash"
