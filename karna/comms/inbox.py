"""File-based inbox for inter-agent communication.

Each agent has an inbox directory at ``~/.karna/comms/inbox/{agent_name}/``.
Messages are stored as ``.md`` files named ``{timestamp}_{id}.md``.

Usage::

    inbox = AgentInbox("alice")
    inbox.send("bob", "hello", "How are you?")

    bob_inbox = AgentInbox("bob")
    unread = bob_inbox.check()         # list of unread AgentMessages
    msg = bob_inbox.read(unread[0].id) # mark as read and return
    bob_inbox.reply(msg, "I'm fine!")   # reply threaded to msg
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from karna.comms.message import AgentMessage
from karna.config import KARNA_DIR

logger = logging.getLogger(__name__)

COMMS_ROOT = KARNA_DIR / "comms" / "inbox"


class AgentInbox:
    """File-based inbox for a single agent.

    Parameters
    ----------
    agent_name:
        The identity of the agent that owns this inbox.
    root:
        Override for the comms root directory (useful for testing).
    """

    def __init__(self, agent_name: str, *, root: Path | None = None) -> None:
        self.agent_name = agent_name
        self._root = root or COMMS_ROOT
        self._inbox_dir = self._root / agent_name
        self._inbox_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def send(
        self,
        to_agent: str,
        subject: str,
        body: str,
        *,
        in_reply_to: str | None = None,
    ) -> AgentMessage:
        """Compose and deliver a message to *to_agent*'s inbox.

        Returns the sent :class:`AgentMessage`.
        """
        msg = AgentMessage(
            from_agent=self.agent_name,
            to_agent=to_agent,
            subject=subject,
            body=body,
            in_reply_to=in_reply_to,
        )
        target_dir = self._root / to_agent
        target_dir.mkdir(parents=True, exist_ok=True)
        mono = time.monotonic_ns()
        filename = f"{msg.timestamp.strftime('%Y%m%dT%H%M%S%f')}_{mono}_{msg.id}.md"
        filepath = target_dir / filename
        filepath.write_text(msg.to_markdown(), encoding="utf-8")
        logger.info("Message %s sent from %s to %s", msg.id, self.agent_name, to_agent)
        return msg

    def check(self, *, include_read: bool = False) -> list[AgentMessage]:
        """Return messages in this agent's inbox.

        By default only unread messages are returned. Pass
        ``include_read=True`` to get everything.

        Messages are sorted oldest-first.
        """
        messages: list[AgentMessage] = []
        if not self._inbox_dir.exists():
            return messages
        for path in sorted(self._inbox_dir.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
                msg = AgentMessage.from_markdown(text)
                if include_read or not msg.read:
                    messages.append(msg)
            except Exception:
                logger.warning("Failed to parse message file: %s", path)
        messages.sort(key=lambda m: m.timestamp)
        return messages

    def read_message(self, message_id: str) -> AgentMessage | None:
        """Find a message by *message_id*, mark it read, and return it.

        Returns ``None`` if the message is not found.
        """
        for path in self._inbox_dir.glob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
                msg = AgentMessage.from_markdown(text)
            except Exception:
                continue
            if msg.id == message_id:
                if not msg.read:
                    msg.read = True
                    path.write_text(msg.to_markdown(), encoding="utf-8")
                return msg
        return None

    def reply(self, original: AgentMessage, body: str, *, subject: str | None = None) -> AgentMessage:
        """Reply to *original*, threading via ``in_reply_to``.

        The reply is delivered to ``original.from_agent``'s inbox.
        """
        reply_subject = subject or f"Re: {original.subject}"
        return self.send(
            to_agent=original.from_agent,
            subject=reply_subject,
            body=body,
            in_reply_to=original.id,
        )

    def get_thread(self, message_id: str) -> list[AgentMessage]:
        """Return all messages in the thread rooted at *message_id*.

        Searches this agent's inbox for messages linked via
        ``in_reply_to``. Results are sorted oldest-first.
        """
        all_msgs = self.check(include_read=True)
        thread: list[AgentMessage] = []
        ids_in_thread = {message_id}
        # Walk forward collecting replies
        changed = True
        while changed:
            changed = False
            for msg in all_msgs:
                if msg.id in ids_in_thread and msg not in thread:
                    thread.append(msg)
                    changed = True
                elif msg.in_reply_to in ids_in_thread and msg.id not in ids_in_thread:
                    ids_in_thread.add(msg.id)
                    thread.append(msg)
                    changed = True
        thread.sort(key=lambda m: m.timestamp)
        return thread
