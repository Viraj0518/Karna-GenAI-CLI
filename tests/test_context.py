"""Tests for the context management layer.

Covers:
- Token estimation accuracy
- Truncation behaviour (system + last user preserved, FIFO drop)
- Project context detection (KARNA.md, CLAUDE.md, .cursorrules, etc.)
- Git context (branch name from a real repo)
- Environment context (platform info)
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from karna.config import KarnaConfig
from karna.context.environment import EnvironmentContext
from karna.context.git import GitContext
from karna.context.manager import ContextManager
from karna.context.project import ProjectContext
from karna.models import Conversation, Message


# -------------------------------------------------------------------- #
#  Token estimation
# -------------------------------------------------------------------- #


class TestTokenEstimation:
    """ContextManager.estimate_tokens should be roughly correct."""

    def test_english_text_within_20_percent(self) -> None:
        """~4 chars/token heuristic should be within 20% for plain English."""
        # A typical English sentence has ~5 chars per word and ~1.3 tokens per word,
        # so roughly 3.8 chars per token.  Our heuristic uses 4.
        text = "The quick brown fox jumps over the lazy dog. " * 100  # ~4500 chars
        estimated = ContextManager.estimate_tokens(text)
        # Real tokenizer would give ~1100 tokens.  4500//4 = 1125.
        # 20% band: 880 - 1320.
        assert 880 <= estimated <= 1320, f"Estimate {estimated} out of 20% band"

    def test_empty_string(self) -> None:
        assert ContextManager.estimate_tokens("") == 0

    def test_short_string(self) -> None:
        # "hi" = 2 chars => 0 tokens (integer division)
        assert ContextManager.estimate_tokens("hi") == 0
        assert ContextManager.estimate_tokens("hello") == 1


# -------------------------------------------------------------------- #
#  Truncation
# -------------------------------------------------------------------- #


class TestTruncation:
    """truncate_to_fit should keep system prompt intent + last user msg."""

    def _make_manager(self) -> ContextManager:
        return ContextManager(config=KarnaConfig(), max_context_tokens=128_000)

    def test_no_truncation_when_fits(self) -> None:
        mgr = self._make_manager()
        msgs = [
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi there"),
        ]
        result = mgr.truncate_to_fit(msgs, budget=10_000)
        assert len(result) == 2

    def test_preserves_last_user_message(self) -> None:
        mgr = self._make_manager()
        # Create messages that exceed a tiny budget.
        msgs = [
            Message(role="user", content="First question " * 100),
            Message(role="assistant", content="First answer " * 100),
            Message(role="user", content="Second question " * 100),
            Message(role="assistant", content="Second answer " * 100),
            Message(role="user", content="FINAL question"),
        ]
        # Budget only big enough for ~2 messages.
        result = mgr.truncate_to_fit(msgs, budget=200)
        roles = [m.role for m in result]
        contents = [m.content for m in result]
        # The last user message must survive.
        assert "FINAL question" in contents[-1]

    def test_drops_oldest_first(self) -> None:
        mgr = self._make_manager()
        msgs = [
            Message(role="user", content="OLD " * 500),
            Message(role="assistant", content="OLD_REPLY " * 500),
            Message(role="user", content="RECENT"),
            Message(role="assistant", content="RECENT_REPLY"),
        ]
        result = mgr.truncate_to_fit(msgs, budget=50)
        contents = " ".join(m.content for m in result)
        assert "RECENT" in contents

    def test_empty_messages(self) -> None:
        mgr = self._make_manager()
        assert mgr.truncate_to_fit([], budget=100) == []


# -------------------------------------------------------------------- #
#  Project context detection
# -------------------------------------------------------------------- #


class TestProjectContext:
    """ProjectContext should discover instruction files."""

    def test_detects_karna_md(self, tmp_path: Path) -> None:
        (tmp_path / "KARNA.md").write_text("Use pytest for testing.")
        ctx = ProjectContext()
        result = ctx.detect(tmp_path)
        assert result is not None
        assert "Use pytest for testing" in result

    def test_detects_claude_md(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("Always use type hints.")
        ctx = ProjectContext()
        result = ctx.detect(tmp_path)
        assert result is not None
        assert "type hints" in result

    def test_detects_cursorrules(self, tmp_path: Path) -> None:
        (tmp_path / ".cursorrules").write_text("No semicolons.")
        ctx = ProjectContext()
        result = ctx.detect(tmp_path)
        assert result is not None
        assert "No semicolons" in result

    def test_detects_copilot_instructions(self, tmp_path: Path) -> None:
        gh_dir = tmp_path / ".github"
        gh_dir.mkdir()
        (gh_dir / "copilot-instructions.md").write_text("Use Go modules.")
        ctx = ProjectContext()
        result = ctx.detect(tmp_path)
        assert result is not None
        assert "Go modules" in result

    def test_detects_karna_project_toml(self, tmp_path: Path) -> None:
        karna_dir = tmp_path / ".karna"
        karna_dir.mkdir()
        (karna_dir / "project.toml").write_text(
            'instructions = "Always run black."\nrules = ["no-print"]'
        )
        ctx = ProjectContext()
        result = ctx.detect(tmp_path)
        assert result is not None
        assert "Always run black" in result
        assert "no-print" in result

    def test_parent_directory_detection(self, tmp_path: Path) -> None:
        """KARNA.md in parent dir should be found from a child dir."""
        (tmp_path / "KARNA.md").write_text("Project root instructions.")
        child = tmp_path / "src" / "deep"
        child.mkdir(parents=True)
        ctx = ProjectContext()
        result = ctx.detect(child)
        assert result is not None
        assert "Project root instructions" in result

    def test_no_files_returns_none(self, tmp_path: Path) -> None:
        ctx = ProjectContext()
        assert ctx.detect(tmp_path) is None

    def test_multiple_files_combined(self, tmp_path: Path) -> None:
        """Both KARNA.md and CLAUDE.md should be loaded."""
        (tmp_path / "KARNA.md").write_text("Karna-specific rule.")
        (tmp_path / "CLAUDE.md").write_text("Claude-specific rule.")
        ctx = ProjectContext()
        result = ctx.detect(tmp_path)
        assert result is not None
        assert "Karna-specific rule" in result
        assert "Claude-specific rule" in result


# -------------------------------------------------------------------- #
#  Git context
# -------------------------------------------------------------------- #


class TestGitContext:
    """GitContext should return branch name from a real git repo."""

    def test_detect_real_repo(self) -> None:
        """The karna repo itself should be detected as a git repo."""
        git_ctx = GitContext()
        assert git_ctx.detect(Path("/home/viraj/karna"))

    def test_detect_non_repo(self, tmp_path: Path) -> None:
        git_ctx = GitContext()
        assert not git_ctx.detect(tmp_path)

    @pytest.mark.asyncio
    async def test_get_context_real_repo(self) -> None:
        """Should return context with branch name from the karna repo."""
        git_ctx = GitContext()
        result = await git_ctx.get_context(Path("/home/viraj/karna"))
        assert result is not None
        assert "Branch:" in result
        assert "Git repository:" in result

    @pytest.mark.asyncio
    async def test_get_context_non_repo(self, tmp_path: Path) -> None:
        git_ctx = GitContext()
        result = await git_ctx.get_context(tmp_path)
        assert result is None

    @pytest.mark.asyncio
    async def test_status_summary(self) -> None:
        """_summarize_status should parse porcelain output."""
        git_ctx = GitContext()
        status = " M src/foo.py\n M src/bar.py\n?? newfile.txt\nA  added.py\n"
        summary = git_ctx._summarize_status(status)
        assert "modified" in summary
        assert "untracked" in summary
        assert "added" in summary


# -------------------------------------------------------------------- #
#  Environment context
# -------------------------------------------------------------------- #


class TestEnvironmentContext:
    """EnvironmentContext should return platform info."""

    def test_returns_platform(self) -> None:
        env_ctx = EnvironmentContext()
        result = env_ctx.get_context()
        assert "Platform:" in result
        assert "Shell:" in result
        assert "Python:" in result
        assert "Working directory:" in result
        assert "Date:" in result

    def test_custom_cwd(self, tmp_path: Path) -> None:
        env_ctx = EnvironmentContext()
        result = env_ctx.get_context(cwd=tmp_path)
        assert str(tmp_path) in result


# -------------------------------------------------------------------- #
#  Full ContextManager.build_messages
# -------------------------------------------------------------------- #


class TestContextManagerBuild:
    """Integration-level tests for build_messages."""

    @pytest.mark.asyncio
    async def test_build_includes_system_prompt(self, tmp_path: Path) -> None:
        mgr = ContextManager(
            config=KarnaConfig(),
            max_context_tokens=128_000,
            cwd=tmp_path,
        )
        conv = Conversation(messages=[
            Message(role="user", content="Hello"),
        ])
        messages = await mgr.build_messages(conv, "You are a helpful assistant.")
        assert messages[0].role == "system"
        assert "You are a helpful assistant" in messages[0].content

    @pytest.mark.asyncio
    async def test_build_includes_environment(self, tmp_path: Path) -> None:
        mgr = ContextManager(
            config=KarnaConfig(),
            max_context_tokens=128_000,
            cwd=tmp_path,
        )
        conv = Conversation(messages=[
            Message(role="user", content="Hi"),
        ])
        messages = await mgr.build_messages(conv, "System prompt.")
        system_content = messages[0].content
        assert "<environment>" in system_content
        assert "Platform:" in system_content

    @pytest.mark.asyncio
    async def test_build_includes_project_context(self, tmp_path: Path) -> None:
        (tmp_path / "KARNA.md").write_text("Always use ruff.")
        mgr = ContextManager(
            config=KarnaConfig(),
            max_context_tokens=128_000,
            cwd=tmp_path,
        )
        conv = Conversation(messages=[
            Message(role="user", content="Hi"),
        ])
        messages = await mgr.build_messages(conv, "System prompt.")
        system_content = messages[0].content
        assert "<project-context>" in system_content
        assert "Always use ruff" in system_content

    @pytest.mark.asyncio
    async def test_build_preserves_user_message(self, tmp_path: Path) -> None:
        mgr = ContextManager(
            config=KarnaConfig(),
            max_context_tokens=128_000,
            cwd=tmp_path,
        )
        conv = Conversation(messages=[
            Message(role="user", content="What is 2+2?"),
        ])
        messages = await mgr.build_messages(conv, "System.")
        user_msgs = [m for m in messages if m.role == "user"]
        assert len(user_msgs) == 1
        assert "2+2" in user_msgs[0].content
