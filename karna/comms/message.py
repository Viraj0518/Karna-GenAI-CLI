"""AgentMessage dataclass with YAML frontmatter serialisation.

Messages are stored as ``.md`` files with YAML frontmatter:

    ---
    id: <uuid>
    from: alice
    to: bob
    subject: greetings
    timestamp: 2026-04-20T12:00:00+00:00
    in_reply_to: <parent-uuid or null>
    read: false
    ---
    Body text goes here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class AgentMessage:
    """A single inter-agent message."""

    from_agent: str
    to_agent: str
    subject: str
    body: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    in_reply_to: str | None = None
    read: bool = False

    # ------------------------------------------------------------------ #
    #  Serialisation helpers
    # ------------------------------------------------------------------ #

    def to_markdown(self) -> str:
        """Serialise to a Markdown string with YAML frontmatter."""
        ts = self.timestamp.isoformat()
        reply = self.in_reply_to or ""
        lines = [
            "---",
            f"id: {self.id}",
            f"from: {self.from_agent}",
            f"to: {self.to_agent}",
            f"subject: {self.subject}",
            f"timestamp: {ts}",
            f"in_reply_to: {reply}",
            f"read: {str(self.read).lower()}",
            "---",
            self.body,
        ]
        return "\n".join(lines) + "\n"

    @classmethod
    def from_markdown(cls, text: str) -> "AgentMessage":
        """Parse a Markdown string with YAML frontmatter into an AgentMessage.

        Uses simple line-based parsing to avoid a PyYAML dependency.
        """
        lines = text.strip().split("\n")
        if not lines or lines[0].strip() != "---":
            raise ValueError("Missing YAML frontmatter opening '---'")

        # Find closing ---
        end_idx = None
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                end_idx = i
                break
        if end_idx is None:
            raise ValueError("Missing YAML frontmatter closing '---'")

        # Parse frontmatter key-value pairs
        meta: dict[str, str] = {}
        for line in lines[1:end_idx]:
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()

        body = "\n".join(lines[end_idx + 1 :]).strip()

        # Parse timestamp
        ts_str = meta.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            ts = datetime.now(timezone.utc)

        in_reply_to = meta.get("in_reply_to", "") or None
        if in_reply_to == "":
            in_reply_to = None

        read_val = meta.get("read", "false").lower() == "true"

        return cls(
            id=meta.get("id", uuid.uuid4().hex[:12]),
            from_agent=meta.get("from", "unknown"),
            to_agent=meta.get("to", "unknown"),
            subject=meta.get("subject", ""),
            body=body,
            timestamp=ts,
            in_reply_to=in_reply_to,
            read=read_val,
        )
