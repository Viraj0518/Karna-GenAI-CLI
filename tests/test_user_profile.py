"""Tests for karna.memory.profile.UserProfile (uses a MockProvider)."""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from karna.memory.memdir import Memdir
from karna.memory.profile import Fact, UserProfile
from karna.models import Message, ModelInfo, StreamEvent


class MockProvider:
    """Minimal provider stub satisfying the structural Protocol."""

    name = "mock"

    def __init__(self, reply_text: str = "") -> None:
        self.reply_text = reply_text
        self.calls: list[list[Message]] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> Message:
        self.calls.append(messages)
        return Message(role="assistant", content=self.reply_text)

    async def stream(  # pragma: no cover - unused
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if False:
            yield StreamEvent(type="done")

    async def list_models(self) -> list[ModelInfo]:  # pragma: no cover
        return []


@pytest.fixture()
def memdir(tmp_path):
    return Memdir(root=tmp_path)


class TestExtractNewFacts:
    @pytest.mark.asyncio
    async def test_extract_parses_bullets(self, memdir):
        reply = "- Lives in Seattle\n- Prefers type hints\n- Uses black with line-length 120"
        prof = UserProfile(memdir, provider=MockProvider(reply_text=reply), model="x")
        facts = await prof.extract_new_facts([Message(role="user", content="hi I'm Viraj from Seattle")])
        assert len(facts) == 3
        assert "Seattle" in facts[0].text
        assert "type hints" in facts[1].text

    @pytest.mark.asyncio
    async def test_extract_none_returns_empty(self, memdir):
        prof = UserProfile(memdir, provider=MockProvider(reply_text="NONE"), model="x")
        facts = await prof.extract_new_facts([Message(role="user", content="hello")])
        assert facts == []

    @pytest.mark.asyncio
    async def test_extract_strips_numbering(self, memdir):
        reply = "1. Lives in Seattle\n2) Uses Python\n * Prefers terse commits"
        prof = UserProfile(memdir, provider=MockProvider(reply_text=reply), model="x")
        facts = await prof.extract_new_facts([Message(role="user", content="x")])
        assert [f.text for f in facts] == [
            "Lives in Seattle",
            "Uses Python",
            "Prefers terse commits",
        ]


class TestMergeFacts:
    def test_merge_writes_new_entry_when_missing(self, memdir):
        prof = UserProfile(memdir)
        prof.merge_facts([Fact("Lives in Seattle"), Fact("Uses Python")])
        body = prof.read()
        assert "Lives in Seattle" in body
        assert "Uses Python" in body

    def test_merge_dedupes_existing(self, memdir):
        prof = UserProfile(memdir)
        prof.merge_facts([Fact("Lives in Seattle")])
        prof.merge_facts([Fact("lives in seattle"), Fact("Uses Python")])
        body = prof.read()
        # "Seattle" line appears exactly once
        seattle_lines = [ln for ln in body.split("\n") if "seattle" in ln.lower()]
        assert len(seattle_lines) == 1
        assert "Uses Python" in body

    def test_merge_empty_is_noop(self, memdir):
        prof = UserProfile(memdir)
        prof.merge_facts([])
        assert prof.read() == ""


class TestRead:
    def test_read_missing_returns_empty(self, memdir):
        prof = UserProfile(memdir)
        assert prof.read() == ""

    def test_read_after_merge_returns_body(self, memdir):
        prof = UserProfile(memdir)
        prof.merge_facts([Fact("Some fact")])
        assert "Some fact" in prof.read()
