"""Tests for ``SessionDB.fork_session``."""

from __future__ import annotations

from pathlib import Path

import pytest

from karna.models import Message
from karna.sessions.db import SessionDB


@pytest.fixture()
def db(tmp_path: Path) -> SessionDB:
    return SessionDB(db_path=tmp_path / "fork.db")


def _seed(db: SessionDB) -> str:
    sid = db.create_session(model="gpt-4o", provider="openai", cwd="/tmp", git_branch="main")
    db.add_message(sid, Message(role="user", content="hello"))
    db.add_message(sid, Message(role="assistant", content="hi there"))
    db.add_message(sid, Message(role="user", content="what's up?"))
    return sid


def test_fork_creates_new_session_id(db: SessionDB) -> None:
    sid = _seed(db)
    new_id = db.fork_session(sid)
    assert isinstance(new_id, str)
    assert len(new_id) == 12
    assert new_id != sid


def test_fork_duplicates_all_messages(db: SessionDB) -> None:
    sid = _seed(db)
    new_id = db.fork_session(sid)

    src_msgs = db.get_session_messages(sid)
    fork_msgs = db.get_session_messages(new_id)

    assert len(fork_msgs) == len(src_msgs) == 3
    for src, forked in zip(src_msgs, fork_msgs, strict=True):
        assert src["role"] == forked["role"]
        assert src["content"] == forked["content"]
        # Message rowids must be fresh — not the same row
        assert src["id"] != forked["id"]


def test_fork_copies_metadata(db: SessionDB) -> None:
    sid = _seed(db)
    new_id = db.fork_session(sid, new_name="follow-up")

    src = db.get_session(sid)
    dst = db.get_session(new_id)
    assert src is not None and dst is not None
    assert dst["model"] == src["model"]
    assert dst["provider"] == src["provider"]
    assert dst["cwd"] == src["cwd"]
    assert dst["git_branch"] == src["git_branch"]
    # New name lives in the summary column; cost counters reset.
    assert dst["summary"] == "follow-up"
    assert dst["total_cost_usd"] == 0.0
    assert dst["total_input_tokens"] == 0
    assert dst["total_output_tokens"] == 0


def test_fork_up_to_message_id(db: SessionDB) -> None:
    sid = _seed(db)
    msgs = db.get_session_messages(sid)
    cutoff = msgs[1]["id"]  # include first two

    new_id = db.fork_session(sid, up_to_message_id=cutoff)
    fork_msgs = db.get_session_messages(new_id)
    assert len(fork_msgs) == 2
    assert fork_msgs[0]["content"] == "hello"
    assert fork_msgs[1]["content"] == "hi there"


def test_fork_missing_source_raises(db: SessionDB) -> None:
    with pytest.raises(KeyError):
        db.fork_session("does-not-exist")


def test_fork_source_unchanged(db: SessionDB) -> None:
    sid = _seed(db)
    db.fork_session(sid)
    # Original messages untouched after a fork.
    assert len(db.get_session_messages(sid)) == 3
