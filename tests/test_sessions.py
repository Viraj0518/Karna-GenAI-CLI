"""Tests for session persistence (SessionDB with SQLite FTS5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from karna.models import Message, ToolCall, ToolResult
from karna.sessions.db import SessionDB


@pytest.fixture()
def db(tmp_path: Path) -> SessionDB:
    """Return a SessionDB backed by a temp file."""
    return SessionDB(db_path=tmp_path / "test.db")


# ------------------------------------------------------------------ #
#  Session lifecycle
# ------------------------------------------------------------------ #


def test_create_session(db: SessionDB) -> None:
    sid = db.create_session(model="gpt-4o", provider="openai", cwd="/tmp")
    assert isinstance(sid, str)
    assert len(sid) == 12


def test_get_session(db: SessionDB) -> None:
    sid = db.create_session(model="gpt-4o", provider="openai", cwd="/tmp")
    session = db.get_session(sid)
    assert session is not None
    assert session["model"] == "gpt-4o"
    assert session["provider"] == "openai"
    assert session["cwd"] == "/tmp"
    assert session["ended_at"] is None


def test_end_session(db: SessionDB) -> None:
    sid = db.create_session(model="gpt-4o", provider="openai", cwd="/tmp")
    db.end_session(sid, summary="Test session")
    session = db.get_session(sid)
    assert session is not None
    assert session["ended_at"] is not None
    assert session["summary"] == "Test session"


def test_delete_session(db: SessionDB) -> None:
    sid = db.create_session(model="gpt-4o", provider="openai", cwd="/tmp")
    db.add_message(sid, Message(role="user", content="hello"))
    assert db.delete_session(sid) is True
    assert db.get_session(sid) is None
    assert db.get_session_messages(sid) == []


def test_delete_nonexistent_session(db: SessionDB) -> None:
    assert db.delete_session("nonexistent") is False


def test_list_sessions(db: SessionDB) -> None:
    for i in range(5):
        db.create_session(model=f"model-{i}", provider="test", cwd="/tmp")
    sessions = db.list_sessions(limit=3)
    assert len(sessions) == 3
    # Should be newest first
    assert sessions[0]["model"] == "model-4"


# ------------------------------------------------------------------ #
#  Messages
# ------------------------------------------------------------------ #


def test_add_and_get_messages(db: SessionDB) -> None:
    sid = db.create_session(model="gpt-4o", provider="openai", cwd="/tmp")
    db.add_message(sid, Message(role="user", content="hello"))
    db.add_message(sid, Message(role="assistant", content="hi there"))

    msgs = db.get_session_messages(sid)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "hello"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "hi there"


def test_add_message_with_tool_calls(db: SessionDB) -> None:
    sid = db.create_session(model="gpt-4o", provider="openai", cwd="/tmp")
    msg = Message(
        role="assistant",
        content="Let me check.",
        tool_calls=[ToolCall(id="tc1", name="bash", arguments={"command": "ls"})],
    )
    db.add_message(sid, msg)
    msgs = db.get_session_messages(sid)
    assert len(msgs) == 1
    assert '"bash"' in msgs[0]["tool_calls"]


def test_add_message_with_tool_results(db: SessionDB) -> None:
    sid = db.create_session(model="gpt-4o", provider="openai", cwd="/tmp")
    msg = Message(
        role="tool",
        tool_results=[ToolResult(tool_call_id="tc1", content="file.txt\nfoo.py")],
    )
    db.add_message(sid, msg)
    msgs = db.get_session_messages(sid)
    assert len(msgs) == 1
    assert "file.txt" in msgs[0]["tool_results"]


# ------------------------------------------------------------------ #
#  FTS5 search
# ------------------------------------------------------------------ #


def test_fts5_search_finds_message(db: SessionDB) -> None:
    sid = db.create_session(model="gpt-4o", provider="openai", cwd="/tmp")
    db.add_message(sid, Message(role="user", content="How do I fix the vLLM batch error?"))
    db.add_message(sid, Message(role="assistant", content="You need to adjust the batch size."))

    results = db.search("vLLM batch")
    assert len(results) >= 1
    matched_content = [r["content"] for r in results]
    assert any("vLLM" in c for c in matched_content)


def test_fts5_search_no_results(db: SessionDB) -> None:
    sid = db.create_session(model="gpt-4o", provider="openai", cwd="/tmp")
    db.add_message(sid, Message(role="user", content="hello world"))
    results = db.search("nonexistent_keyword_xyz")
    assert results == []


def test_fts5_search_tool_results(db: SessionDB) -> None:
    sid = db.create_session(model="gpt-4o", provider="openai", cwd="/tmp")
    msg = Message(
        role="tool",
        tool_results=[ToolResult(tool_call_id="tc1", content="CUDA out of memory error on GPU 0")],
    )
    db.add_message(sid, msg)
    results = db.search("CUDA memory")
    assert len(results) >= 1


# ------------------------------------------------------------------ #
#  Resume
# ------------------------------------------------------------------ #


def test_resume_session(db: SessionDB) -> None:
    sid = db.create_session(model="gpt-4o", provider="openai", cwd="/tmp")
    db.add_message(sid, Message(role="user", content="hello"))
    db.add_message(sid, Message(role="assistant", content="hi there"))
    db.add_message(
        sid,
        Message(
            role="assistant",
            content="checking",
            tool_calls=[ToolCall(id="tc1", name="bash", arguments={"command": "ls"})],
        ),
    )

    conv = db.resume_session(sid)
    assert conv is not None
    assert len(conv.messages) == 3
    assert conv.messages[0].role == "user"
    assert conv.messages[0].content == "hello"
    assert conv.messages[1].role == "assistant"
    assert conv.messages[2].tool_calls[0].name == "bash"
    assert conv.model == "gpt-4o"
    assert conv.provider == "openai"


def test_resume_nonexistent_session(db: SessionDB) -> None:
    conv = db.resume_session("nonexistent")
    assert conv is None


def test_get_latest_session_id(db: SessionDB) -> None:
    assert db.get_latest_session_id() is None
    db.create_session(model="m1", provider="p1", cwd="/tmp")
    sid2 = db.create_session(model="m2", provider="p2", cwd="/tmp")
    assert db.get_latest_session_id() == sid2


# ------------------------------------------------------------------ #
#  Cost aggregation
# ------------------------------------------------------------------ #


def test_update_and_get_session_cost(db: SessionDB) -> None:
    sid = db.create_session(model="gpt-4o", provider="openai", cwd="/tmp")
    db.update_session_cost(sid, input_tokens=100, output_tokens=50, cost_usd=0.005)
    db.update_session_cost(sid, input_tokens=200, output_tokens=100, cost_usd=0.010)

    session = db.get_session(sid)
    assert session is not None
    assert session["total_input_tokens"] == 300
    assert session["total_output_tokens"] == 150
    assert abs(session["total_cost_usd"] - 0.015) < 1e-9


def test_get_total_cost(db: SessionDB) -> None:
    sid1 = db.create_session(model="gpt-4o", provider="openai", cwd="/tmp")
    sid2 = db.create_session(model="claude", provider="anthropic", cwd="/tmp")
    db.update_session_cost(sid1, input_tokens=100, output_tokens=50, cost_usd=0.005)
    db.update_session_cost(sid2, input_tokens=200, output_tokens=100, cost_usd=0.010)

    total = db.get_total_cost()
    assert total["input_tokens"] == 300
    assert total["output_tokens"] == 150
    assert abs(total["cost_usd"] - 0.015) < 1e-9
    assert total["session_count"] == 2


def test_get_cost_by_model(db: SessionDB) -> None:
    sid1 = db.create_session(model="gpt-4o", provider="openai", cwd="/tmp")
    sid2 = db.create_session(model="claude", provider="anthropic", cwd="/tmp")
    db.update_session_cost(sid1, input_tokens=100, output_tokens=50, cost_usd=0.005)
    db.update_session_cost(sid2, input_tokens=200, output_tokens=100, cost_usd=0.010)

    by_model = db.get_cost_by_model(days=30)
    assert "gpt-4o" in by_model
    assert "claude" in by_model
    assert by_model["gpt-4o"]["input_tokens"] == 100
    assert by_model["claude"]["input_tokens"] == 200
