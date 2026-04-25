"""Multi-agent communication system for Karna.

Provides a file-based inbox system that allows agents to send
messages to each other using ``.md`` files with YAML frontmatter
stored in ``~/.karna/comms/inbox/{agent}/``.
"""

from karna.comms.inbox import AgentInbox
from karna.comms.message import AgentMessage

__all__ = ["AgentInbox", "AgentMessage"]
