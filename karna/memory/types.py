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


def parse_memory_type(raw: str | None) -> MemoryType | None:
    """Parse a raw frontmatter value into a MemoryType.

    Invalid or missing values return None -- legacy files without a
    ``type:`` field keep working, files with unknown types degrade
    gracefully.
    """
    if raw is None or raw not in MEMORY_TYPES:
        return None
    return raw  # type: ignore[return-value]


class MemoryEntry(BaseModel):
    """A single memory entry with YAML frontmatter metadata."""

    name: str
    description: str  # one-line, used for relevance matching
    type: MemoryType
    content: str
    created_at: datetime
    updated_at: datetime
    file_path: Path
