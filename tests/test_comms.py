"""Tests for the multi-agent communication system.

Covers:
- AgentMessage serialisation round-trips
- AgentInbox send/receive between two agents
- Message persistence to disk
- Reply threading via in_reply_to
- CommsTool actions (send/check/read/reply)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from karna.comms.inbox import AgentInbox
from karna.comms.message import AgentMessage
from karna.tools.comms import CommsTool

# --------------------------------------------------------------------------- #
#  AgentMessage tests
# --------------------------------------------------------------------------- #


class TestAgentMessage:
    def test_round_trip_markdown(self) -> None:
        msg = AgentMessage(
            from_agent="alice",
            to_agent="bob",
            subject="hello",
            body="How are you?",
        )
        md = msg.to_markdown()
        restored = AgentMessage.from_markdown(md)
        assert restored.from_agent == "alice"
        assert restored.to_agent == "bob"
        assert restored.subject == "hello"
        assert restored.body == "How are you?"
        assert restored.id == msg.id
        assert restored.read is False

    def test_from_markdown_with_reply(self) -> None:
        md = (
            "---\n"
            "id: abc123\n"
            "from: bob\n"
            "to: alice\n"
            "subject: Re: hello\n"
            "timestamp: 2026-04-20T12:00:00+00:00\n"
            "in_reply_to: xyz789\n"
            "read: true\n"
            "---\n"
            "I'm fine!\n"
        )
        msg = AgentMessage.from_markdown(md)
        assert msg.id == "abc123"
        assert msg.from_agent == "bob"
        assert msg.to_agent == "alice"
        assert msg.subject == "Re: hello"
        assert msg.in_reply_to == "xyz789"
        assert msg.read is True
        assert msg.body == "I'm fine!"

    def test_from_markdown_missing_frontmatter_raises(self) -> None:
        with pytest.raises(ValueError, match="Missing YAML frontmatter"):
            AgentMessage.from_markdown("no frontmatter here")

    def test_from_markdown_unclosed_frontmatter_raises(self) -> None:
        with pytest.raises(ValueError, match="Missing YAML frontmatter closing"):
            AgentMessage.from_markdown("---\nid: x\nfrom: a\n")


# --------------------------------------------------------------------------- #
#  AgentInbox tests
# --------------------------------------------------------------------------- #


class TestAgentInbox:
    def test_send_receive_between_agents(self, tmp_path: Path) -> None:
        """Alice sends a message to Bob; Bob sees it in his inbox."""
        alice = AgentInbox("alice", root=tmp_path)
        bob = AgentInbox("bob", root=tmp_path)

        alice.send("bob", "greetings", "Hello Bob!")

        unread = bob.check()
        assert len(unread) == 1
        assert unread[0].from_agent == "alice"
        assert unread[0].to_agent == "bob"
        assert unread[0].subject == "greetings"
        assert unread[0].body == "Hello Bob!"

    def test_message_persistence(self, tmp_path: Path) -> None:
        """Messages survive re-instantiating the inbox."""
        alice = AgentInbox("alice", root=tmp_path)
        alice.send("bob", "persist test", "This should persist")

        # Re-create Bob's inbox from the same root
        bob = AgentInbox("bob", root=tmp_path)
        unread = bob.check()
        assert len(unread) == 1
        assert unread[0].body == "This should persist"

    def test_read_marks_message(self, tmp_path: Path) -> None:
        """Reading a message marks it as read and removes it from unread."""
        alice = AgentInbox("alice", root=tmp_path)
        bob = AgentInbox("bob", root=tmp_path)

        sent = alice.send("bob", "test", "read me")

        # Before reading
        assert len(bob.check()) == 1

        # Read it
        msg = bob.read_message(sent.id)
        assert msg is not None
        assert msg.read is True

        # After reading, check() returns empty (unread only)
        assert len(bob.check()) == 0

        # But include_read=True still shows it
        assert len(bob.check(include_read=True)) == 1

    def test_reply_threading(self, tmp_path: Path) -> None:
        """Reply creates a message threaded to the original via in_reply_to."""
        alice = AgentInbox("alice", root=tmp_path)
        bob = AgentInbox("bob", root=tmp_path)

        original = alice.send("bob", "question", "What time is it?")
        bob_msg = bob.read_message(original.id)
        assert bob_msg is not None

        reply = bob.reply(bob_msg, "It's noon!")
        assert reply.in_reply_to == original.id
        assert reply.to_agent == "alice"
        assert reply.subject == "Re: question"

        # Alice should see the reply
        alice_unread = alice.check()
        assert len(alice_unread) == 1
        assert alice_unread[0].in_reply_to == original.id

    def test_read_nonexistent_returns_none(self, tmp_path: Path) -> None:
        bob = AgentInbox("bob", root=tmp_path)
        assert bob.read_message("nonexistent") is None

    def test_multiple_messages_ordered(self, tmp_path: Path) -> None:
        """Multiple messages are returned oldest-first."""
        alice = AgentInbox("alice", root=tmp_path)
        bob = AgentInbox("bob", root=tmp_path)

        alice.send("bob", "first", "msg 1")
        alice.send("bob", "second", "msg 2")
        alice.send("bob", "third", "msg 3")

        unread = bob.check()
        assert len(unread) == 3
        assert unread[0].subject == "first"
        assert unread[2].subject == "third"

    def test_get_thread(self, tmp_path: Path) -> None:
        """get_thread collects the full reply chain."""
        alice = AgentInbox("alice", root=tmp_path)
        bob = AgentInbox("bob", root=tmp_path)

        original = alice.send("bob", "chain", "start")
        bob_msg = bob.read_message(original.id)
        assert bob_msg is not None
        reply1 = bob.reply(bob_msg, "reply 1")

        # Alice reads reply and replies back
        alice_msg = alice.read_message(reply1.id)
        assert alice_msg is not None
        alice.reply(alice_msg, "reply 2")

        # Thread from bob's perspective includes all messages in his inbox
        thread = bob.get_thread(original.id)
        assert len(thread) >= 1  # at least the original


# --------------------------------------------------------------------------- #
#  CommsTool tests
# --------------------------------------------------------------------------- #


class TestCommsTool:
    @pytest.mark.asyncio
    async def test_send_action(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("karna.comms.inbox.COMMS_ROOT", tmp_path)
        tool = CommsTool(agent_name="sender")
        tool._inbox = AgentInbox("sender", root=tmp_path)

        result = await tool.execute(action="send", to="receiver", subject="hi", body="hello world")
        data = json.loads(result)
        assert data["status"] == "sent"
        assert data["to"] == "receiver"

        # Verify message was delivered
        receiver = AgentInbox("receiver", root=tmp_path)
        msgs = receiver.check()
        assert len(msgs) == 1
        assert msgs[0].body == "hello world"

    @pytest.mark.asyncio
    async def test_check_action(self, tmp_path: Path) -> None:
        # Deliver a message first
        sender = AgentInbox("sender", root=tmp_path)
        sender.send("checker", "test", "check me")

        tool = CommsTool(agent_name="checker")
        tool._inbox = AgentInbox("checker", root=tmp_path)

        result = await tool.execute(action="check")
        data = json.loads(result)
        assert data["unread"] == 1
        assert data["messages"][0]["from"] == "sender"

    @pytest.mark.asyncio
    async def test_read_action(self, tmp_path: Path) -> None:
        sender = AgentInbox("sender", root=tmp_path)
        sent = sender.send("reader", "test", "read me")

        tool = CommsTool(agent_name="reader")
        tool._inbox = AgentInbox("reader", root=tmp_path)

        result = await tool.execute(action="read", message_id=sent.id)
        data = json.loads(result)
        assert data["body"] == "read me"
        assert data["read"] is True

    @pytest.mark.asyncio
    async def test_reply_action(self, tmp_path: Path) -> None:
        sender = AgentInbox("sender", root=tmp_path)
        sent = sender.send("replier", "question", "any thoughts?")

        tool = CommsTool(agent_name="replier")
        tool._inbox = AgentInbox("replier", root=tmp_path)

        result = await tool.execute(action="reply", message_id=sent.id, body="yes!")
        data = json.loads(result)
        assert data["status"] == "sent"
        assert data["in_reply_to"] == sent.id

        # Sender should see the reply
        sender_msgs = sender.check()
        assert len(sender_msgs) == 1
        assert sender_msgs[0].body == "yes!"

    @pytest.mark.asyncio
    async def test_send_missing_fields(self, tmp_path: Path) -> None:
        tool = CommsTool(agent_name="x")
        tool._inbox = AgentInbox("x", root=tmp_path)

        result = await tool.execute(action="send")
        assert result.startswith("[error]")

        result = await tool.execute(action="send", to="y")
        assert result.startswith("[error]")

    @pytest.mark.asyncio
    async def test_read_missing_id(self, tmp_path: Path) -> None:
        tool = CommsTool(agent_name="x")
        tool._inbox = AgentInbox("x", root=tmp_path)

        result = await tool.execute(action="read")
        assert result.startswith("[error]")

    @pytest.mark.asyncio
    async def test_check_empty_inbox(self, tmp_path: Path) -> None:
        tool = CommsTool(agent_name="lonely")
        tool._inbox = AgentInbox("lonely", root=tmp_path)

        result = await tool.execute(action="check")
        data = json.loads(result)
        assert data["unread"] == 0
