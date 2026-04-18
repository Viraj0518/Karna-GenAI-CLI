"""Tests for karna.memory.index.MemoryIndex."""

from __future__ import annotations

import pytest

from karna.memory.index import MemoryIndex
from karna.memory.memdir import Memdir


@pytest.fixture()
def memdir(tmp_path):
    return Memdir(root=tmp_path)


@pytest.fixture()
def index(tmp_path):
    return MemoryIndex(root=tmp_path)


class TestRebuild:
    def test_rebuild_from_three_entries(self, memdir, index):
        memdir.add(type="user", name="User profile", description="who Viraj is", body="x")
        memdir.add(type="feedback", name="Coding style", description="terse commits", body="y")
        memdir.add(type="reference", name="APIs", description="OpenRouter endpoints", body="z")

        index.rebuild_from_memdir(memdir)
        content = index.read()

        assert "# Memory Index" in content
        assert "User profile" in content
        assert "who Viraj is" in content
        assert "Coding style" in content
        assert "terse commits" in content
        assert "APIs" in content
        # Three bullet lines
        bullets = [ln for ln in content.split("\n") if ln.startswith("- [")]
        assert len(bullets) == 3

    def test_rebuild_is_idempotent(self, memdir, index):
        memdir.add(type="user", name="A", description="d", body="b")
        index.rebuild_from_memdir(memdir)
        first = index.read()
        index.rebuild_from_memdir(memdir)
        assert index.read() == first


class TestAddRemoveEntry:
    def test_add_entry_appends(self, index):
        index.add_entry("user_profile.md", "who the user is", name="User profile")
        index.add_entry("feedback_style.md", "terse commits", name="Style")
        content = index.read()
        assert "user_profile.md" in content
        assert "feedback_style.md" in content
        bullets = [ln for ln in content.split("\n") if ln.startswith("- [")]
        assert len(bullets) == 2

    def test_add_entry_dedupes_by_filename(self, index):
        index.add_entry("user_profile.md", "first desc", name="User profile")
        index.add_entry("user_profile.md", "second desc", name="User profile")
        content = index.read()
        bullets = [ln for ln in content.split("\n") if "user_profile.md" in ln]
        assert len(bullets) == 1

    def test_remove_entry_deletes_right_line(self, index):
        index.add_entry("a.md", "alpha", name="A")
        index.add_entry("b.md", "beta", name="B")
        index.add_entry("c.md", "gamma", name="C")
        index.remove_entry("b.md")
        content = index.read()
        assert "a.md" in content
        assert "b.md" not in content
        assert "c.md" in content

    def test_read_returns_file_content(self, index):
        assert index.read() == ""  # missing -> empty
        index.add_entry("x.md", "desc", name="X")
        assert "x.md" in index.read()
        assert index.read().startswith("# Memory Index")
