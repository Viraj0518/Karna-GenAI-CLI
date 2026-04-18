"""Tests for karna.memory.memdir.Memdir."""

from __future__ import annotations

import pytest

from karna.memory.memdir import Memdir


@pytest.fixture()
def memdir(tmp_path):
    return Memdir(root=tmp_path)


class TestAddUpdateDelete:
    def test_add_round_trip(self, memdir):
        fn = memdir.add(
            type="feedback",
            name="Terse commits",
            description="Short commit messages please",
            body="Prefer <70 char summaries, imperative mood.",
        )
        assert fn.endswith(".md")
        assert fn.startswith("feedback_")
        mem = memdir.get(fn)
        assert mem.type == "feedback"
        assert mem.name == "Terse commits"
        assert "imperative" in mem.body

    def test_update_changes_body_and_last_updated(self, memdir):
        fn = memdir.add(type="project", name="Ctx", description="d", body="old body")
        mem_before = memdir.get(fn)
        memdir.update(fn, "new body content")
        mem_after = memdir.get(fn)
        assert "new body content" in mem_after.body
        assert mem_after.last_updated >= mem_before.last_updated

    def test_delete(self, memdir):
        fn = memdir.add(type="reference", name="x", description="d", body="b")
        memdir.delete(fn)
        assert not (memdir.root / fn).exists()

    def test_invalid_type_raises(self, memdir):
        with pytest.raises(ValueError):
            memdir.add(type="bogus", name="n", description="d", body="b")  # type: ignore[arg-type]


class TestListAndSearch:
    def test_list_by_type(self, memdir):
        memdir.add(type="user", name="A", description="d", body="alpha cat")
        memdir.add(type="feedback", name="B", description="d", body="beta dog")
        memdir.add(type="feedback", name="C", description="d", body="gamma fish")

        users = memdir.list(type="user")
        fb = memdir.list(type="feedback")
        assert len(users) == 1
        assert len(fb) == 2

    def test_list_all(self, memdir):
        memdir.add(type="user", name="A", description="d", body="x")
        memdir.add(type="reference", name="B", description="d", body="y")
        assert len(memdir.list()) == 2

    def test_search_finds_keyword(self, memdir):
        memdir.add(type="reference", name="OpenRouter", description="api", body="sk keys")
        memdir.add(type="reference", name="Anthropic", description="docs", body="claude stuff")
        hits = memdir.search("openrouter")
        assert hits, "search should return at least one hit"
        assert hits[0].name == "OpenRouter"

    def test_search_empty_query_returns_list(self, memdir):
        memdir.add(type="user", name="A", description="d", body="x")
        hits = memdir.search("   ")
        assert len(hits) == 1


class TestSecretScrubbing:
    def test_scrub_applied_on_write(self, memdir):
        secret = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdef"
        fn = memdir.add(
            type="reference",
            name="api key stash",
            description="totally fine",
            body=f"API key is {secret} do not share",
        )
        mem = memdir.get(fn)
        assert secret not in mem.body
        assert "<REDACTED_SECRET>" in mem.body

    def test_scrub_applied_on_update(self, memdir):
        fn = memdir.add(type="reference", name="x", description="d", body="clean")
        secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
        memdir.update(fn, f"leaked {secret} oops")
        mem = memdir.get(fn)
        assert secret not in mem.body
