"""Tests for KARNA.md project instructions (E8).

Covers:
- KARNA.md detection in all 3 locations (project root, .karna/, global)
- Hierarchical merge (project overrides global)
- CLAUDE.md / .cursorrules compatibility
- ``/memory`` slash commands
- ``nellie init`` enhancements
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from karna.cli import app
from karna.context.project import ProjectContext
from karna.models import Conversation


runner = CliRunner()


# --------------------------------------------------------------------------- #
#  KARNA.md detection
# --------------------------------------------------------------------------- #


class TestKarnaMdDetection:
    """KARNA.md should be found in project root, .karna/, and global."""

    def test_detect_karna_md_in_project_root(self, tmp_path: Path) -> None:
        (tmp_path / "KARNA.md").write_text("# Project rules\nAlways use pytest.\n")
        ctx = ProjectContext()
        result = ctx.detect(tmp_path)
        assert result is not None
        assert "Always use pytest" in result
        assert "karna project instructions" in result

    def test_detect_karna_md_in_dot_karna(self, tmp_path: Path) -> None:
        karna_dir = tmp_path / ".karna"
        karna_dir.mkdir()
        (karna_dir / "KARNA.md").write_text("# Dot-karna rules\nUse ruff.\n")
        ctx = ProjectContext()
        result = ctx.detect(tmp_path)
        assert result is not None
        assert "Use ruff" in result

    def test_detect_global_karna_md(self, tmp_path: Path) -> None:
        global_karna = tmp_path / ".karna" / "KARNA.md"
        global_karna.parent.mkdir(parents=True)
        global_karna.write_text("# Global rules\nBe concise.\n")

        # Patch the global path
        with patch("karna.context.project._GLOBAL_KARNA_MD", global_karna):
            ctx = ProjectContext()
            # Use a project dir that has NO KARNA.md
            project = tmp_path / "myproject"
            project.mkdir()
            result = ctx.detect(project)
            assert result is not None
            assert "Be concise" in result
            assert "global" in result.lower()

    def test_no_karna_md_anywhere(self, tmp_path: Path) -> None:
        with patch("karna.context.project._GLOBAL_KARNA_MD", tmp_path / "nonexistent"):
            ctx = ProjectContext()
            result = ctx.detect(tmp_path)
            assert result is None


# --------------------------------------------------------------------------- #
#  Hierarchical merge
# --------------------------------------------------------------------------- #


class TestHierarchicalMerge:
    """Project-level KARNA.md should override global."""

    def test_project_overrides_global(self, tmp_path: Path) -> None:
        # Create global KARNA.md
        global_karna = tmp_path / "global" / ".karna" / "KARNA.md"
        global_karna.parent.mkdir(parents=True)
        global_karna.write_text("# Global\nGlobal rule: be verbose.\n")

        # Create project KARNA.md
        project = tmp_path / "project"
        project.mkdir()
        (project / "KARNA.md").write_text("# Project\nProject rule: be concise.\n")

        with patch("karna.context.project._GLOBAL_KARNA_MD", global_karna):
            ctx = ProjectContext()
            result = ctx.detect(project)
            assert result is not None
            # Project KARNA.md should be present
            assert "be concise" in result
            # Global should NOT be loaded since project-level exists
            assert "be verbose" not in result

    def test_global_loaded_when_no_project(self, tmp_path: Path) -> None:
        global_karna = tmp_path / "global" / ".karna" / "KARNA.md"
        global_karna.parent.mkdir(parents=True)
        global_karna.write_text("# Global\nGlobal fallback rule.\n")

        project = tmp_path / "project"
        project.mkdir()

        with patch("karna.context.project._GLOBAL_KARNA_MD", global_karna):
            ctx = ProjectContext()
            result = ctx.detect(project)
            assert result is not None
            assert "Global fallback rule" in result

    def test_both_project_and_claude_md(self, tmp_path: Path) -> None:
        """KARNA.md and CLAUDE.md should both be loaded, KARNA.md first."""
        (tmp_path / "KARNA.md").write_text("# Karna rules\nKarna takes priority.\n")
        (tmp_path / "CLAUDE.md").write_text("# Claude rules\nClaude compat.\n")

        with patch("karna.context.project._GLOBAL_KARNA_MD", tmp_path / "nonexistent"):
            ctx = ProjectContext()
            result = ctx.detect(tmp_path)
            assert result is not None
            assert "Karna takes priority" in result
            assert "Claude compat" in result
            # KARNA.md should appear before CLAUDE.md
            karna_pos = result.index("Karna takes priority")
            claude_pos = result.index("Claude compat")
            assert karna_pos < claude_pos


# --------------------------------------------------------------------------- #
#  Compatibility with CLAUDE.md and .cursorrules
# --------------------------------------------------------------------------- #


class TestCompatibility:
    """CLAUDE.md and .cursorrules should be detected and loaded."""

    def test_claude_md_loaded(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("# Claude\nUse Claude Code conventions.\n")
        with patch("karna.context.project._GLOBAL_KARNA_MD", tmp_path / "nonexistent"):
            ctx = ProjectContext()
            result = ctx.detect(tmp_path)
            assert result is not None
            assert "Claude Code conventions" in result

    def test_cursorrules_loaded(self, tmp_path: Path) -> None:
        (tmp_path / ".cursorrules").write_text("Always format with prettier.\n")
        with patch("karna.context.project._GLOBAL_KARNA_MD", tmp_path / "nonexistent"):
            ctx = ProjectContext()
            result = ctx.detect(tmp_path)
            assert result is not None
            assert "format with prettier" in result

    def test_karna_md_higher_priority_than_claude_md(self, tmp_path: Path) -> None:
        """KARNA.md should appear before CLAUDE.md in merged output."""
        (tmp_path / "KARNA.md").write_text("Karna first.\n")
        (tmp_path / "CLAUDE.md").write_text("Claude second.\n")
        (tmp_path / ".cursorrules").write_text("Cursor third.\n")

        with patch("karna.context.project._GLOBAL_KARNA_MD", tmp_path / "nonexistent"):
            ctx = ProjectContext()
            result = ctx.detect(tmp_path)
            assert result is not None

            karna_pos = result.index("Karna first")
            claude_pos = result.index("Claude second")
            cursor_pos = result.index("Cursor third")
            assert karna_pos < claude_pos < cursor_pos


# --------------------------------------------------------------------------- #
#  /memory slash commands
# --------------------------------------------------------------------------- #


class TestMemorySlashCommands:
    """Test the /memory slash command suite."""

    @pytest.fixture()
    def memory_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "memory"
        d.mkdir()
        return d

    @pytest.fixture()
    def setup_memories(self, memory_dir: Path) -> Path:
        from karna.memory.manager import MemoryManager

        mm = MemoryManager(memory_dir=memory_dir)
        mm.save_memory(
            name="User Role",
            type="user",
            description="User is a backend engineer",
            content="The user is a senior backend engineer working on microservices.",
        )
        mm.save_memory(
            name="Use Ruff",
            type="feedback",
            description="Always use ruff for linting",
            content="The user prefers ruff over flake8 for all Python linting.",
        )
        return memory_dir

    def test_memory_list(self, setup_memories: Path) -> None:
        from karna.tui.slash import _cmd_memory

        console = Console(force_terminal=True, width=120)
        with patch("karna.memory.manager.MemoryManager.__init__", return_value=None):
            from karna.memory.manager import MemoryManager as RealMM

            real_mm = RealMM.__new__(RealMM)
            real_mm.memory_dir = setup_memories
            with patch("karna.memory.MemoryManager", return_value=real_mm):
                _cmd_memory(console=console, args="")

    def test_memory_search(self, setup_memories: Path) -> None:
        from karna.tui.slash import _cmd_memory

        console = Console(force_terminal=True, width=120)
        from karna.memory.manager import MemoryManager

        real_mm = MemoryManager(memory_dir=setup_memories)
        with patch("karna.memory.MemoryManager", return_value=real_mm):
            _cmd_memory(console=console, args="search ruff")

    def test_memory_show(self, setup_memories: Path) -> None:
        from karna.tui.slash import _cmd_memory

        console = Console(force_terminal=True, width=120)
        from karna.memory.manager import MemoryManager

        real_mm = MemoryManager(memory_dir=setup_memories)
        with patch("karna.memory.MemoryManager", return_value=real_mm):
            _cmd_memory(console=console, args="show Use Ruff")

    def test_memory_forget(self, setup_memories: Path) -> None:
        from karna.memory.manager import MemoryManager
        from karna.tui.slash import _cmd_memory

        console = Console(force_terminal=True, width=120)
        mm = MemoryManager(memory_dir=setup_memories)

        with patch("karna.memory.MemoryManager", return_value=mm):
            _cmd_memory(console=console, args="forget Use Ruff")

        # Verify it was deleted
        remaining = mm.load_all()
        assert all(e.name != "Use Ruff" for e in remaining)

    def test_memory_show_not_found(self, setup_memories: Path) -> None:
        from karna.tui.slash import _cmd_memory

        console = Console(force_terminal=True, width=120)
        from karna.memory.manager import MemoryManager

        real_mm = MemoryManager(memory_dir=setup_memories)
        with patch("karna.memory.MemoryManager", return_value=real_mm):
            # Should not raise
            _cmd_memory(console=console, args="show nonexistent_memory")

    def test_memory_search_empty(self, memory_dir: Path) -> None:
        from karna.tui.slash import _cmd_memory

        console = Console(force_terminal=True, width=120)
        from karna.memory.manager import MemoryManager

        real_mm = MemoryManager(memory_dir=memory_dir)
        with patch("karna.memory.MemoryManager", return_value=real_mm):
            _cmd_memory(console=console, args="search nothing_matches_xyz")


# --------------------------------------------------------------------------- #
#  nellie init enhancements
# --------------------------------------------------------------------------- #


class TestNellieInit:
    """Test the enhanced nellie init command."""

    def test_init_minimal_template(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["init", "--minimal"])
        assert result.exit_code == 0, result.output
        karna_md = tmp_path / "KARNA.md"
        assert karna_md.exists()
        content = karna_md.read_text()
        assert "Project Instructions for Nellie" in content
        assert "## Conventions" in content
        assert "## Tools & Stack" in content
        assert "## Rules" in content

    def test_init_creates_dot_karna(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.output
        assert (tmp_path / ".karna").is_dir()

    def test_init_detects_existing_karna_md(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "KARNA.md").write_text("# existing\n")
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "already exists" in result.output
        # Original content preserved
        assert (tmp_path / "KARNA.md").read_text() == "# existing\n"

    def test_init_detects_claude_md(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CLAUDE.md").write_text("# Claude\n")
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "CLAUDE.md" in result.output
        assert "existing AI config" in result.output


# --------------------------------------------------------------------------- #
#  Memory injection (system prompt)
# --------------------------------------------------------------------------- #


class TestMemoryInjection:
    """Verify memory context flows into the system prompt."""

    def test_memory_context_in_prompt(self) -> None:
        """Memory context should appear in the system prompt."""
        from karna.prompts.system import _build_context_sections

        sections = _build_context_sections(
            project_context="project ctx",
            git_context="git ctx",
            memory_context="## My memories\nUser prefers ruff.",
            custom_instructions="custom inst",
        )

        labels = [s[0] for s in sections]
        assert "Memory" in labels

        # Memory should be lowest priority (trimmed first)
        memory_section = next(s for s in sections if s[0] == "Memory")
        assert memory_section[2] == 5  # priority 5


# --------------------------------------------------------------------------- #
#  Memory staleness
# --------------------------------------------------------------------------- #


class TestMemoryStaleness:
    """Verify staleness warnings are generated for old memories."""

    def test_staleness_warning_for_old_memory(self) -> None:
        import time

        from karna.memory.manager import _staleness_warning

        # 10 days ago
        old_mtime = time.time() - (10 * 86400)
        warning = _staleness_warning(old_mtime)
        assert warning is not None
        assert "10 days old" in warning

    def test_no_staleness_for_recent_memory(self) -> None:
        import time

        from karna.memory.manager import _staleness_warning

        recent_mtime = time.time() - (2 * 86400)  # 2 days ago
        warning = _staleness_warning(recent_mtime)
        assert warning is None
