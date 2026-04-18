"""E2E memory persistence across sessions.

Session 1 writes a memory ("favorite color is blue") to
``<tmp>/.karna/memory/``. Session 2 starts fresh, loads the memory
context, and the mock provider sees the memory injected as a system
prompt -- we assert it answers "blue".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from karna.agents.loop import agent_loop_sync
from karna.memory.manager import MemoryManager
from karna.models import Conversation, Message

from .conftest import MockProvider


def _memory_aware_responder(memory_context: str) -> Message:
    """Simulate a model that reads the injected memory context and
    answers based on it. In a real run the memory text is inside the
    system prompt -- here we check the content directly."""
    if "blue" in memory_context.lower():
        return Message(role="assistant", content="Your favorite color is blue.")
    return Message(role="assistant", content="I don't know your favorite color.")


@pytest.mark.asyncio
async def test_memory_survives_across_sessions(tmp_path: Path) -> None:
    memory_dir = tmp_path / "karna-memory"
    memory_dir.mkdir()
    mgr = MemoryManager(memory_dir=memory_dir)

    # ------------------------------------------------------------------
    # Session 1 -- user states a preference and it's saved as a memory.
    # (In production this write happens via an agent-triggered memory
    # tool; here we call the manager directly, which is the same
    # on-disk surface.)
    # ------------------------------------------------------------------
    session_1_provider = MockProvider([Message(role="assistant", content="Got it -- I'll remember that.")])
    session_1_conv = Conversation(messages=[Message(role="user", content="my favorite color is blue")])
    await agent_loop_sync(session_1_provider, session_1_conv, [])

    mgr.save_memory(
        name="favorite color",
        type="user",
        description="User's preferred color",
        content="The user's favorite color is blue.",
    )

    # Verify on-disk state
    md_files = list(memory_dir.glob("*.md"))
    assert len(md_files) >= 1
    assert (memory_dir / "MEMORY.md").exists()

    # ------------------------------------------------------------------
    # Session 2 -- fresh conversation, memory manager reloads from disk.
    # ------------------------------------------------------------------
    mgr2 = MemoryManager(memory_dir=memory_dir)
    entries = mgr2.load_all()
    assert len(entries) >= 1
    assert any("blue" in e.content.lower() for e in entries)

    memory_prompt = mgr2.get_context_for_prompt(max_tokens=2000)
    assert "blue" in memory_prompt.lower()

    # The agent loop for session 2 uses the memory context as part of
    # the system prompt. We simulate that injection and assert the
    # provider can produce a grounded answer.
    session_2_provider = MockProvider([_memory_aware_responder(memory_prompt)])
    session_2_conv = Conversation(messages=[Message(role="user", content="what's my favorite color?")])

    result = await agent_loop_sync(
        session_2_provider,
        session_2_conv,
        [],
        system_prompt=memory_prompt,
    )

    assert "blue" in result.content.lower()
    # The provider saw the memory-bearing system prompt on the call.
    assert session_2_provider.seen_system_prompts
    assert session_2_provider.seen_system_prompts[0] is not None
    assert "blue" in session_2_provider.seen_system_prompts[0].lower()


@pytest.mark.asyncio
async def test_memory_search_finds_saved_entry(tmp_path: Path) -> None:
    """The search surface returns the entry we just saved."""
    memory_dir = tmp_path / "karna-memory"
    memory_dir.mkdir()
    mgr = MemoryManager(memory_dir=memory_dir)

    mgr.save_memory(
        name="prefers terse replies",
        type="feedback",
        description="User prefers short, terse answers",
        content="Be concise. Skip preamble.",
    )

    hits = mgr.search("terse")
    assert len(hits) >= 1
    assert any("terse" in (h.name + h.content).lower() for h in hits)
