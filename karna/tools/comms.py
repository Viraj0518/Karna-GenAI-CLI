"""CommsTool — inter-agent messaging via the file-based inbox system.

Exposes send, check, read, and reply actions so the LLM agent can
communicate with other agents through the standard tool interface.
"""

from __future__ import annotations

import json
from typing import Any

from karna.comms.inbox import AgentInbox
from karna.config import load_config
from karna.tools.base import BaseTool

# Cap single-message size at 1 MB. A model-generated 1 GB payload would
# exhaust inbox disk before any other limit kicks in; 1 MB covers every
# legitimate message we've seen by two orders of magnitude.
_MAX_MESSAGE_BYTES = 1_000_000


def _body_exceeds_limit(body: str) -> bool:
    """Return True iff ``body`` would serialise to more than the cap.

    UTF-8 uses 1–4 bytes per code point, so a string with fewer than
    ``_MAX_MESSAGE_BYTES // 4`` characters cannot possibly exceed the
    cap — short-circuit without allocating the full ``.encode("utf-8")``
    copy in the common case. Only strings past that threshold pay the
    encoding cost to confirm exactly.
    """
    if len(body) * 4 <= _MAX_MESSAGE_BYTES:
        return False
    return len(body.encode("utf-8")) > _MAX_MESSAGE_BYTES


class CommsTool(BaseTool):
    """Send, check, read, and reply to inter-agent messages.

    Actions:
    - ``send``  — send a message to another agent
    - ``check`` — list unread messages in this agent's inbox
    - ``read``  — read a specific message by ID (marks it read)
    - ``reply`` — reply to a message by ID
    """

    name = "comms"
    description = (
        "Inter-agent messaging. Send messages to other agents, "
        "check your inbox for unread messages, read a specific "
        "message, or reply to a message."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["send", "check", "read", "reply"],
                "description": "Action to perform.",
            },
            "to": {
                "type": "string",
                "description": "Recipient agent name. Required for 'send'.",
            },
            "subject": {
                "type": "string",
                "description": "Message subject. Required for 'send'.",
            },
            "body": {
                "type": "string",
                "description": "Message body. Required for 'send' and 'reply'.",
            },
            "message_id": {
                "type": "string",
                "description": "Message ID. Required for 'read' and 'reply'.",
            },
        },
        "required": ["action"],
    }

    def __init__(self, *, agent_name: str | None = None) -> None:
        super().__init__()
        if agent_name is None:
            agent_name = load_config().agent.name
        self._agent_name = agent_name
        self._inbox = AgentInbox(agent_name)

    async def execute(self, **kwargs: Any) -> str:
        action: str = kwargs.get("action", "check")

        if action == "send":
            return self._handle_send(**kwargs)
        elif action == "check":
            return self._handle_check()
        elif action == "read":
            return self._handle_read(**kwargs)
        elif action == "reply":
            return self._handle_reply(**kwargs)
        else:
            return f"[error] Unknown action: {action!r}. Use send/check/read/reply."

    def _handle_send(self, **kwargs: Any) -> str:
        to = kwargs.get("to", "")
        subject = kwargs.get("subject", "")
        body = kwargs.get("body", "")
        if not to:
            return "[error] 'to' is required for send action."
        if not subject:
            return "[error] 'subject' is required for send action."
        if not body:
            return "[error] 'body' is required for send action."
        if _body_exceeds_limit(body):
            return (
                f"[error] Message body exceeds the {_MAX_MESSAGE_BYTES:,}-byte "
                "limit. Split the content across multiple messages or save it "
                "to a file and share the path."
            )
        try:
            msg = self._inbox.send(to, subject, body)
        except ValueError as exc:
            return f"[error] {exc}"
        return json.dumps(
            {
                "status": "sent",
                "id": msg.id,
                "to": msg.to_agent,
                "subject": msg.subject,
            }
        )

    def _handle_check(self) -> str:
        messages = self._inbox.check()
        if not messages:
            return json.dumps({"status": "ok", "unread": 0, "messages": []})
        items = [
            {
                "id": m.id,
                "from": m.from_agent,
                "subject": m.subject,
                "timestamp": m.timestamp.isoformat(),
            }
            for m in messages
        ]
        return json.dumps({"status": "ok", "unread": len(items), "messages": items})

    def _handle_read(self, **kwargs: Any) -> str:
        message_id = kwargs.get("message_id", "")
        if not message_id:
            return "[error] 'message_id' is required for read action."
        msg = self._inbox.read_message(message_id)
        if msg is None:
            return f"[error] Message not found: {message_id}"
        return json.dumps(
            {
                "id": msg.id,
                "from": msg.from_agent,
                "to": msg.to_agent,
                "subject": msg.subject,
                "body": msg.body,
                "timestamp": msg.timestamp.isoformat(),
                "in_reply_to": msg.in_reply_to,
                "read": msg.read,
            }
        )

    def _handle_reply(self, **kwargs: Any) -> str:
        message_id = kwargs.get("message_id", "")
        body = kwargs.get("body", "")
        if not message_id:
            return "[error] 'message_id' is required for reply action."
        if not body:
            return "[error] 'body' is required for reply action."
        if _body_exceeds_limit(body):
            return (
                f"[error] Reply body exceeds the {_MAX_MESSAGE_BYTES:,}-byte "
                "limit. Split the content across multiple messages or save it "
                "to a file and share the path."
            )
        # Read the original message first
        original = self._inbox.read_message(message_id)
        if original is None:
            return f"[error] Message not found: {message_id}"
        reply = self._inbox.reply(original, body)
        return json.dumps(
            {
                "status": "sent",
                "id": reply.id,
                "to": reply.to_agent,
                "subject": reply.subject,
                "in_reply_to": reply.in_reply_to,
            }
        )
