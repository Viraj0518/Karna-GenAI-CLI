"""Auto-persistent memory system (4 typed entries, MEMORY.md index).

Ported from Claude Code's memdir architecture (cc-src).
"""

from karna.memory.manager import MemoryManager
from karna.memory.types import MEMORY_TYPES, MemoryEntry, MemoryType

__all__ = ["MemoryManager", "MemoryEntry", "MemoryType", "MEMORY_TYPES"]

# Lazy import for the extractor — avoids circular imports while
# keeping it discoverable via ``from karna.memory import MemoryExtractor``.


def __getattr__(name: str):  # noqa: ANN204
    if name == "MemoryExtractor":
        from karna.memory.extractor import MemoryExtractor

        return MemoryExtractor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
