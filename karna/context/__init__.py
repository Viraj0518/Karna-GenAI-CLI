"""Context management — windowing, project detection, git awareness.

The ``ContextManager`` assembles the full context window sent to
providers on every turn, handling token budget, project config
detection (KARNA.md / CLAUDE.md / .cursorrules / copilot-instructions),
git repo state injection, and environment metadata.

Adapted from cc-src context patterns.  See NOTICES.md for attribution.
"""

from karna.context.manager import ContextManager

__all__ = ["ContextManager"]
