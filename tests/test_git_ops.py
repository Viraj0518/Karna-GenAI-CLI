"""Tests for GitTool — git operations with safety guards.

Each test creates a fresh temporary git repo to avoid polluting the
real project history.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from karna.tools.git_ops import GitTool

# ======================================================================= #
#  Helpers
# ======================================================================= #


def _make_repo(tmp: str) -> str:
    """Initialise a git repo in *tmp* with one commit and return the path."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_EMAIL": "test@test.com",
    }
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp, check=True, capture_output=True, env=env)
    readme = Path(tmp) / "README.md"
    readme.write_text("# test repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=tmp, check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=tmp, check=True, capture_output=True, env=env)
    return tmp


def _tool(cwd: str) -> GitTool:
    return GitTool(cwd=cwd)


# ======================================================================= #
#  Status
# ======================================================================= #


class TestStatus:
    @pytest.mark.asyncio
    async def test_clean_tree(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            result = await _tool(td).execute(action="status")
            assert "Branch: main" in result
            assert "clean" in result.lower()

    @pytest.mark.asyncio
    async def test_dirty_tree(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            (Path(td) / "new.txt").write_text("hello")
            result = await _tool(td).execute(action="status")
            assert "Branch: main" in result
            assert "new.txt" in result


# ======================================================================= #
#  Diff
# ======================================================================= #


class TestDiff:
    @pytest.mark.asyncio
    async def test_unstaged_diff(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            readme = Path(td) / "README.md"
            readme.write_text("# changed\n")
            result = await _tool(td).execute(action="diff")
            assert "changed" in result

    @pytest.mark.asyncio
    async def test_staged_diff_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            readme = Path(td) / "README.md"
            readme.write_text("# staged change\n")
            subprocess.run(["git", "add", "README.md"], cwd=td, check=True, capture_output=True)
            result = await _tool(td).execute(action="diff")
            assert "staged" in result.lower()

    @pytest.mark.asyncio
    async def test_no_changes(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            result = await _tool(td).execute(action="diff")
            assert "no changes" in result.lower()


# ======================================================================= #
#  Log
# ======================================================================= #


class TestLog:
    @pytest.mark.asyncio
    async def test_shows_commits(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            result = await _tool(td).execute(action="log")
            assert "initial commit" in result

    @pytest.mark.asyncio
    async def test_custom_count(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            result = await _tool(td).execute(action="log", args="-1")
            lines = [ln for ln in result.strip().splitlines() if ln.strip()]
            assert len(lines) == 1


# ======================================================================= #
#  Add
# ======================================================================= #


class TestAdd:
    @pytest.mark.asyncio
    async def test_stage_specific_files(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            (Path(td) / "a.txt").write_text("a")
            (Path(td) / "b.txt").write_text("b")
            result = await _tool(td).execute(action="add", files=["a.txt"])
            assert "staged" in result.lower()
            # Verify only a.txt is staged
            proc = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=td,
                capture_output=True,
                text=True,
            )
            assert "a.txt" in proc.stdout
            assert "b.txt" not in proc.stdout

    @pytest.mark.asyncio
    async def test_add_no_files_errors(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            result = await _tool(td).execute(action="add")
            assert "[error]" in result


# ======================================================================= #
#  Commit
# ======================================================================= #


class TestCommit:
    @pytest.mark.asyncio
    async def test_commit_with_message(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            (Path(td) / "x.txt").write_text("x")
            subprocess.run(["git", "add", "x.txt"], cwd=td, check=True, capture_output=True)
            result = await _tool(td).execute(action="commit", message="add x file")
            assert "x.txt" in result or "add x file" in result
            # Verify co-author trailer
            proc = subprocess.run(
                ["git", "log", "-1", "--format=%B"],
                cwd=td,
                capture_output=True,
                text=True,
            )
            assert "Co-Authored-By:" in proc.stdout

    @pytest.mark.asyncio
    async def test_commit_empty_message_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            result = await _tool(td).execute(action="commit", message="")
            assert "[error]" in result

    @pytest.mark.asyncio
    async def test_commit_amend_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            result = await _tool(td).execute(action="commit", message="amend", args="--amend")
            assert "[BLOCKED]" in result


# ======================================================================= #
#  Safety guards
# ======================================================================= #


class TestSafetyGuards:
    @pytest.mark.asyncio
    async def test_force_push_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            # Attempt via args smuggling
            result = await _tool(td).execute(action="checkout", args="main && git push --force")
            assert "[BLOCKED]" in result

    @pytest.mark.asyncio
    async def test_force_push_short_flag_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            result = await _tool(td).execute(action="checkout", args="main && git push -f origin main")
            assert "[BLOCKED]" in result

    @pytest.mark.asyncio
    async def test_reset_hard_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            result = await _tool(td).execute(action="checkout", args="main && git reset --hard HEAD~1")
            assert "[BLOCKED]" in result


# ======================================================================= #
#  Branch
# ======================================================================= #


class TestBranch:
    @pytest.mark.asyncio
    async def test_list_branches(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            result = await _tool(td).execute(action="branch")
            assert "main" in result

    @pytest.mark.asyncio
    async def test_create_branch(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            result = await _tool(td).execute(action="branch", args="feature/new")
            assert "feature/new" in result or "Switched" in result

    @pytest.mark.asyncio
    async def test_dirty_tree_warning(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            (Path(td) / "dirty.txt").write_text("uncommitted")
            result = await _tool(td).execute(action="branch", args="other")
            assert "[warning]" in result


# ======================================================================= #
#  Stash
# ======================================================================= #


class TestStash:
    @pytest.mark.asyncio
    async def test_stash_list_empty(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            result = await _tool(td).execute(action="stash", args="list")
            # Empty stash returns empty string
            assert "error" not in result.lower() or result.strip() == ""

    @pytest.mark.asyncio
    async def test_stash_push_pop(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            (Path(td) / "README.md").write_text("# modified\n")
            result = await _tool(td).execute(action="stash", args="push")
            assert "error" not in result.lower()

            # Verify clean after stash
            status = await _tool(td).execute(action="status")
            assert "clean" in status.lower()

            # Pop it back
            result = await _tool(td).execute(action="stash", args="pop")
            assert "error" not in result.lower()


# ======================================================================= #
#  Checkout
# ======================================================================= #


class TestCheckout:
    @pytest.mark.asyncio
    async def test_checkout_branch(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            env = {
                **os.environ,
                "GIT_AUTHOR_NAME": "Test",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "test@test.com",
                "GIT_COMMITTER_EMAIL": "test@test.com",
            }
            subprocess.run(["git", "branch", "other"], cwd=td, check=True, capture_output=True, env=env)
            result = await _tool(td).execute(action="checkout", args="other")
            assert "other" in result.lower() or "switched" in result.lower()

    @pytest.mark.asyncio
    async def test_checkout_no_branch_errors(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            result = await _tool(td).execute(action="checkout")
            assert "[error]" in result

    @pytest.mark.asyncio
    async def test_checkout_dirty_tree_warning(self):
        with tempfile.TemporaryDirectory() as td:
            _make_repo(td)
            env = {
                **os.environ,
                "GIT_AUTHOR_NAME": "Test",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "test@test.com",
                "GIT_COMMITTER_EMAIL": "test@test.com",
            }
            subprocess.run(["git", "branch", "other"], cwd=td, check=True, capture_output=True, env=env)
            (Path(td) / "dirty.txt").write_text("uncommitted")
            result = await _tool(td).execute(action="checkout", args="other")
            assert "[warning]" in result
