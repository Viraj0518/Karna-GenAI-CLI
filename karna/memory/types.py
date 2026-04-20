"""Memory type taxonomy and entry model.

Memories are constrained to four types capturing context NOT derivable
from the current project state. Code patterns, architecture, git history,
and file structure are derivable (via grep/git/KARNA.md) and should NOT
be saved as memories.

Ported from cc-src memoryTypes.ts.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

MEMORY_TYPES = ["user", "feedback", "project", "reference"]

MemoryType = Literal["user", "feedback", "project", "reference"]


def parse_memory_type(
    raw: str | None,
    allowed_types: list[str] | None = None,
) -> str | None:
    """Parse a raw frontmatter value into a validated memory type.

    Parameters
    ----------
    raw : str or None
        The raw ``type`` value from YAML frontmatter.
    allowed_types : list[str], optional
        Custom list of valid types (e.g. from ``MemoryConfig.types``).
        When ``None``, falls back to the built-in ``MEMORY_TYPES``.

    Invalid or missing values return None -- legacy files without a
    ``type:`` field keep working, files with unknown types degrade
    gracefully.
    """
    if raw is None:
        return None
    valid = allowed_types if allowed_types is not None else MEMORY_TYPES
    if raw not in valid:
        return None
    return raw


class MemoryEntry(BaseModel):
    """A single memory entry with YAML frontmatter metadata."""

    name: str
    description: str  # one-line, used for relevance matching
    type: str  # built-in MemoryType or custom type from config
    content: str
    created_at: datetime
    updated_at: datetime
    file_path: Path
