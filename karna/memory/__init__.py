"""Auto-persistent memory system (4 typed entries, MEMORY.md index).

Ported from Claude Code's memdir architecture (cc-src).
"""

from karna.memory.manager import MemoryManager
from karna.memory.types import MEMORY_TYPES, MemoryEntry, MemoryType

__all__ = ["MemoryManager", "MemoryEntry", "MemoryType", "MEMORY_TYPES"]
