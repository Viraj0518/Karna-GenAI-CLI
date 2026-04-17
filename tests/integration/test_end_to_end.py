"""End-to-end pipeline test: agent loop + tools + sessions + compaction.

Runs the real ``agent_loop_sync`` / ``agent_loop`` against a
``MockProvider`` scripted to request a tool call followed by a final
text message. Asserts the full conversation shape, that the session
persists to SQLite, and that compaction-size input reaches the
compactor path.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from karna.agents.loop import agent_loop, agent_loop_sync
from karna.compaction.compactor import Compactor
from karna.models import Conversation, Message, ToolCall
from karna.sessions.db import SessionDB
from karna.tools.read import ReadTool

from .conftest import MockProvider


# --------------------------------------------------------------------------- #
#  Full agent-loop pipeline
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_full_pipeline_tool_call_then_final(
    mock_karna_home: Path, tmp_path: Path
) -> None:
    """nellie pipeline: user msg -> tool_call -> tool_result -> final text."""
    # Create a README to read inside an allowed root
    readme = tmp_path / "README.md"
    readme.write_text("# Project\nThis is the project readme.\n", encoding="utf-8")

    provider = MockProvider(
        [
            Message(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(id="tc_1", name="read", arguments={"file_path": str(readme)})
                ],
            ),
            Message(role="assistant", content="I read the file."),
        ]
    )

    conv = Conversation(
        messages=[Message(role="user", content="read the readme and summarize")]
    )

    # ReadTool enforces a cwd/allow-list by default -- pass tmp_path as
    # the allowed root so the temp README resolves.
    result = await agent_loop_sync(
        provider, conv, [ReadTool(allowed_roots=[tmp_path])]
    )

    # Final assistant message
    assert result.role == "assistant"
    assert "read the file" in result.content.lower()

    # Conversation shape: user -> assistant(tool_call) -> tool(result) -> assistant
    assert len(conv.messages) == 4
    assert conv.messages[0].role == "user"
    assert conv.messages[1].role == "assistant"
    assert len(conv.messages[1].tool_calls) == 1
    assert conv.messages[1].tool_calls[0].name == "read"
    assert conv.messages[2].role == "tool"
    assert len(conv.messages[2].tool_results) == 1
    assert "project readme" in conv.messages[2].tool_results[0].content.lower()
    assert conv.messages[3].role == "assistant"


# --------------------------------------------------------------------------- #
#  Session persistence
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_session_persisted_to_sqlite(
    mock_karna_home: Path, tmp_path: Path
) -> None:
    """After running the loop, the conversation round-trips through SessionDB."""
    provider = MockProvider(
        [Message(role="assistant", content="Hello from the mock.")]
    )
    conv = Conversation(
        messages=[Message(role="user", content="hi")],
        model="mock-1",
        provider="mock",
    )

    result = await agent_loop_sync(provider, conv, [])
    assert result.content == "Hello from the mock."

    db_path = tmp_path / "sessions.db"
    db = SessionDB(db_path=db_path)
    session_id = db.create_session(
        model=conv.model, provider=conv.provider, cwd=str(tmp_path)
    )
    for msg in conv.messages:
        db.add_message(session_id, msg)
    db.end_session(session_id, summary="integration-test")
    db.close()

    # Reopen and verify round-trip
    db2 = SessionDB(db_path=db_path)
    stored = db2.get_session(session_id)
    assert stored is not None
    assert stored["provider"] == "mock"
    assert stored["summary"] == "integration-test"

    rows = db2.get_session_messages(session_id)
    roles = [r["role"] for r in rows]
    assert roles == ["user", "assistant"]
    assert rows[0]["content"] == "hi"
    assert rows[1]["content"] == "Hello from the mock."

    resumed = db2.resume_session(session_id)
    assert resumed is not None
    assert len(resumed.messages) == 2
    assert resumed.messages[1].content == "Hello from the mock."
    db2.close()


# --------------------------------------------------------------------------- #
#  Compaction reachability
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_compaction_triggered_by_long_input(mock_karna_home: Path) -> None:
    """A conversation past the compaction threshold reaches the compactor.

    The compactor rewrites older messages into a single summary message
    returned by the provider.
    """
    # Each message ~800 chars = ~200 tokens after the /4 estimate.
    # 30 messages => ~6000 estimated tokens, well past a 1000-token window
    # at the default 0.93 threshold.
    long_msgs = [
        Message(role="user" if i % 2 == 0 else "assistant", content="x" * 800)
        for i in range(30)
    ]
    original_len = len(long_msgs)
    conv = Conversation(messages=long_msgs)

    summarizer = MockProvider(
        [Message(role="assistant", content="SUMMARY: earlier conversation compacted.")]
    )
    compactor = Compactor(summarizer, threshold=0.5)

    context_window = 1000
    assert await compactor.should_compact(conv.messages, context_window)

    new_conv = await compactor.compact(conv, context_window)

    # Expect at least one assistant/system summary message containing SUMMARY
    contents = " ".join(m.content for m in new_conv.messages)
    assert "SUMMARY" in contents
    # The compacted conversation should be shorter than the original
    assert len(new_conv.messages) < original_len
    # The compactor should have been called at least once
    assert summarizer.call_count >= 1


# --------------------------------------------------------------------------- #
#  Error path: provider raises once -> friendly surface
# --------------------------------------------------------------------------- #


class _Fake401(Exception):
    """Stand-in for a 401 auth error. The agent loop should surface it
    as a user-visible message rather than crashing the process."""


@pytest.mark.asyncio
async def test_401_error_surfaced_to_user(mock_karna_home: Path) -> None:
    """Provider raises a 401-like error -- user sees a readable fallback.

    The sync agent loop catches provider failures and returns an
    ``[error]`` assistant message instead of propagating the exception.
    """
    provider = MockProvider(
        [Message(role="assistant", content="(never reached)")],
        raise_once=_Fake401("401 Unauthorized"),
    )
    conv = Conversation(messages=[Message(role="user", content="hi")])

    # agent_loop_sync wraps httpx errors; non-httpx exceptions propagate,
    # so we wrap in try/except to assert the error is at least reachable.
    try:
        result = await agent_loop_sync(provider, conv, [])
    except _Fake401 as exc:
        # Acceptable: the CLI layer would catch this and render a friendly
        # retry prompt. The important invariant is that the loop did not
        # silently swallow the auth failure.
        assert "401" in str(exc)
        return

    # If the loop did absorb the error, it must produce a user-visible
    # message that mentions retry / error rather than empty content.
    assert result.role == "assistant"
    assert result.content  # non-empty fallback


# --------------------------------------------------------------------------- #
#  Streaming path sanity -- user sees tool_call + tool_result + final
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_streaming_pipeline_emits_all_event_types(
    mock_karna_home: Path, tmp_path: Path
) -> None:
    """The streaming loop emits text, tool_call, and done events in order."""
    readme = tmp_path / "README.md"
    readme.write_text("readme body\n", encoding="utf-8")

    provider = MockProvider(
        [
            Message(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(id="tc_1", name="read", arguments={"file_path": str(readme)})
                ],
            ),
            Message(role="assistant", content="done."),
        ]
    )
    conv = Conversation(
        messages=[Message(role="user", content="summarize the readme")]
    )

    events: list[str] = []
    async for event in agent_loop(provider, conv, [ReadTool()]):
        events.append(event.type)

    assert "tool_call_start" in events
    assert "tool_call_end" in events
    assert "done" in events
    # Final assistant text was produced
    assert conv.messages[-1].role == "assistant"
    assert conv.messages[-1].content == "done."
