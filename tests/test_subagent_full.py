"""Tests for ``karna.agents.subagent.spawn_subagent``.

Covers the full subagent spawn lifecycle: non-isolated runs, worktree
isolation (creation + cleanup, even on exception), non-git fallback,
and concurrent spawns without path collision.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from karna.agents.subagent import spawn_subagent
from karna.config import KarnaConfig
from karna.models import Message, ModelInfo, StreamEvent
from karna.providers.base import BaseProvider

# --------------------------------------------------------------------------- #
#  Minimal mock provider — returns a canned final message.
# --------------------------------------------------------------------------- #


class _DoneProvider(BaseProvider):
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


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #


def _make_git_repo(path: Path) -> None:
    """Initialise a minimal git repo with one commit at *path*."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
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
async def test_spawn_subagent_none_isolation_runs_in_parent_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = _DoneProvider(content="hello from subagent")

    result = await spawn_subagent(
        "do a thing",
        parent_config=KarnaConfig(),
        parent_provider=provider,
        tools=[],
        isolation="none",
    )

    assert result == "hello from subagent"
    assert provider.calls == 1
    # cwd unchanged
    assert Path(os.getcwd()).resolve() == tmp_path.resolve()


@pytest.mark.asyncio
async def test_spawn_subagent_worktree_creates_and_cleans(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_git_repo(repo)
    monkeypatch.chdir(repo)

    worktree_base = tmp_path / "wts"
    provider = _DoneProvider(content="ok")

    # Capture the worktree path by snooping the base dir mid-run.
    observed: dict[str, Path] = {}

    class _Watcher(_DoneProvider):
        async def complete(self, messages, tools=None, **kw):
            # At this point the worktree should exist and be cwd.
            observed["cwd"] = Path(os.getcwd())
            children = list(worktree_base.iterdir()) if worktree_base.exists() else []
            observed["children"] = children
            return await super().complete(messages, tools, **kw)

    provider = _Watcher(content="ok")

    result = await spawn_subagent(
        "go",
        parent_config=KarnaConfig(),
        parent_provider=provider,
        tools=[],
        isolation="worktree",
        worktree_base=worktree_base,
    )

    assert result == "ok"
    # Worktree was created under the expected base
    assert observed["children"], "worktree dir should exist during subagent run"
    created = observed["children"][0]
    assert created.name.startswith("karna-worktree-")
    # And cwd was the worktree during the run
    assert observed["cwd"].resolve() == created.resolve()
    # After return: cwd restored and worktree cleaned up
    assert Path(os.getcwd()).resolve() == repo.resolve()
    assert not created.exists(), f"worktree {created} should have been removed"


@pytest.mark.asyncio
async def test_spawn_subagent_worktree_cleans_on_exception(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_git_repo(repo)
    monkeypatch.chdir(repo)

    worktree_base = tmp_path / "wts"

    class _Boom(_DoneProvider):
        async def complete(self, messages, tools=None, **kw):
            raise RuntimeError("kaboom")

    provider = _Boom()

    # spawn_subagent swallows exceptions and returns [error] string
    result = await spawn_subagent(
        "go",
        parent_config=KarnaConfig(),
        parent_provider=provider,
        tools=[],
        isolation="worktree",
        worktree_base=worktree_base,
    )

    assert result.startswith("[error]")
    # cwd restored
    assert Path(os.getcwd()).resolve() == repo.resolve()
    # No stray worktree directory left behind
    leftover = list(worktree_base.iterdir()) if worktree_base.exists() else []
    assert leftover == [], f"expected cleanup, found {leftover}"


@pytest.mark.asyncio
async def test_spawn_subagent_non_git_falls_back_to_none(tmp_path, monkeypatch, caplog):
    # tmp_path is NOT a git repo
    monkeypatch.chdir(tmp_path)
    provider = _DoneProvider(content="fallback ok")

    with caplog.at_level("WARNING", logger="karna.agents.subagent"):
        result = await spawn_subagent(
            "go",
            parent_config=KarnaConfig(),
            parent_provider=provider,
            tools=[],
            isolation="worktree",
            worktree_base=tmp_path / "wts",
        )

    assert result == "fallback ok"
    # cwd unchanged — fallback means we run in the parent cwd
    assert Path(os.getcwd()).resolve() == tmp_path.resolve()
    assert any("not a git repo" in rec.message for rec in caplog.records), "expected a fallback warning to be logged"


@pytest.mark.asyncio
async def test_spawn_subagent_concurrent_no_collision(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_git_repo(repo)
    monkeypatch.chdir(repo)

    worktree_base = tmp_path / "wts"

    # Each concurrent call must observe its own unique worktree path.
    seen_paths: list[Path] = []
    lock = asyncio.Lock()

    class _Recorder(_DoneProvider):
        async def complete(self, messages, tools=None, **kw):
            async with lock:
                seen_paths.append(Path(os.getcwd()))
            # Small yield so both coroutines overlap inside the context.
            await asyncio.sleep(0.05)
            return Message(role="assistant", content="ok")

    provider_a = _Recorder()
    provider_b = _Recorder()

    results = await asyncio.gather(
        spawn_subagent(
            "a",
            parent_config=KarnaConfig(),
            parent_provider=provider_a,
            tools=[],
            isolation="worktree",
            worktree_base=worktree_base,
        ),
        spawn_subagent(
            "b",
            parent_config=KarnaConfig(),
            parent_provider=provider_b,
            tools=[],
            isolation="worktree",
            worktree_base=worktree_base,
        ),
    )

    assert results == ["ok", "ok"]
    # Two distinct worktree cwds observed
    assert len(seen_paths) == 2
    assert seen_paths[0].resolve() != seen_paths[1].resolve(), f"concurrent spawns collided on path: {seen_paths}"
    # Both worktrees cleaned up
    leftover = list(worktree_base.iterdir()) if worktree_base.exists() else []
    assert leftover == [], f"expected cleanup, found {leftover}"
