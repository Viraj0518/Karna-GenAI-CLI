"""MemoryManager -- persistent file-based memory with YAML frontmatter.

Ported from cc-src memoryScan.ts + memoryAge.ts.

Each memory is a ``.md`` file with YAML frontmatter (name, description,
type).  A ``MEMORY.md`` index lives alongside the files with one-line
pointers.

Layout::

    ~/.karna/memory/
        MEMORY.md          <- index (injected into every system prompt)
        user_role.md       <- individual memory files
        feedback_terse.md
        ...
"""

from __future__ import annotations

import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from karna.memory.types import MEMORY_TYPES, MemoryEntry, MemoryType, parse_memory_type
from karna.security.guards import scrub_secrets

# --------------------------------------------------------------------------- #
#  Frontmatter parser (lightweight, no PyYAML dependency)
# --------------------------------------------------------------------------- #

_FM_FENCE = re.compile(r"^---\s*$")


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML frontmatter from a markdown file.

    Returns (frontmatter_dict, body).  If no valid frontmatter is found,
    returns an empty dict and the full text as body.
    """
    lines = text.split("\n")
    if not lines or not _FM_FENCE.match(lines[0]):
        return {}, text

    end_idx: int | None = None
    for i in range(1, len(lines)):
        if _FM_FENCE.match(lines[i]):
            end_idx = i
            break

    if end_idx is None:
        return {}, text

    fm: dict[str, str] = {}
    for line in lines[1:end_idx]:
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip()

    body = "\n".join(lines[end_idx + 1 :]).strip()
    return fm, body


def _render_frontmatter(fm: dict[str, str], body: str) -> str:
    """Render a frontmatter dict + body into a markdown string."""
    parts = ["---"]
    for k, v in fm.items():
        parts.append(f"{k}: {v}")
    parts.append("---")
    parts.append("")
    parts.append(body)
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------- #
#  Age helpers (ported from memoryAge.ts)
# --------------------------------------------------------------------------- #

_SECONDS_PER_DAY = 86_400


def _memory_age_days(mtime: float) -> int:
    """Days since mtime.  Floor-rounded, clamped to 0."""
    now = datetime.now(timezone.utc).timestamp()
    return max(0, math.floor((now - mtime) / _SECONDS_PER_DAY))


def _memory_age_text(mtime: float) -> str:
    """Human-readable age string."""
    d = _memory_age_days(mtime)
    if d == 0:
        return "today"
    if d == 1:
        return "yesterday"
    return f"{d} days ago"


def _staleness_warning(mtime: float, threshold_days: int = 7) -> str | None:
    """Return a staleness warning if memory is older than *threshold_days*."""
    d = _memory_age_days(mtime)
    if d < threshold_days:
        return None
    return (
        f"This memory is {d} days old. "
        "Memories are point-in-time observations, not live state -- "
        "claims about code behavior or file:line citations may be outdated. "
        "Verify against current code before asserting as fact."
    )


# --------------------------------------------------------------------------- #
#  Filename helper
# --------------------------------------------------------------------------- #

def _slugify(text: str) -> str:
    """Convert a title to a filesystem-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower().strip())
    slug = slug.strip("_")
    return slug[:60] or "memory"


# --------------------------------------------------------------------------- #
#  MemoryManager
# --------------------------------------------------------------------------- #

_INDEX_NAME = "MEMORY.md"
_MAX_MEMORY_FILES = 200
_TOKEN_CHARS = 4  # rough chars-per-token estimate


class MemoryManager:
    """Persistent, file-based memory system.

    Parameters
    ----------
    memory_dir : Path, optional
        Root directory for memory files.  Defaults to ``~/.karna/memory``.
    """

    def __init__(self, memory_dir: Path | None = None) -> None:
        self.memory_dir = memory_dir or Path.home() / ".karna" / "memory"

    def _ensure_dir(self) -> None:
        """Create the memory directory if it doesn't exist."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _index_path(self) -> Path:
        return self.memory_dir / _INDEX_NAME

    # ------------------------------------------------------------------ #
    #  Index
    # ------------------------------------------------------------------ #

    def load_index(self) -> str:
        """Load MEMORY.md index -- this goes into every system prompt."""
        if not self._index_path.exists():
            return ""
        return self._index_path.read_text(encoding="utf-8")

    def _write_index(self, content: str) -> None:
        """Write the MEMORY.md index."""
        self._ensure_dir()
        self._index_path.write_text(content, encoding="utf-8")

    def _add_index_entry(self, name: str, filename: str, description: str) -> None:
        """Append a pointer line to the MEMORY.md index."""
        index = self.load_index()
        if not index:
            index = "# Memory Index\n\n"
        line = f"- [{name}]({filename}) -- {description}\n"
        # Avoid duplicates
        if filename in index:
            return
        self._write_index(index.rstrip("\n") + "\n" + line)

    def _remove_index_entry(self, filename: str) -> None:
        """Remove a file's pointer from the MEMORY.md index."""
        index = self.load_index()
        if not index:
            return
        lines = index.split("\n")
        filtered = [ln for ln in lines if filename not in ln]
        self._write_index("\n".join(filtered))

    # ------------------------------------------------------------------ #
    #  Load
    # ------------------------------------------------------------------ #

    def load_all(self) -> list[MemoryEntry]:
        """Load all memory files with frontmatter parsing.

        Returns entries sorted newest-first, capped at _MAX_MEMORY_FILES.
        """
        if not self.memory_dir.exists():
            return []

        entries: list[MemoryEntry] = []
        md_files = [
            f
            for f in self.memory_dir.rglob("*.md")
            if f.name != _INDEX_NAME
        ]

        for fp in md_files:
            try:
                text = fp.read_text(encoding="utf-8")
                fm, body = _parse_frontmatter(text)
                mem_type = parse_memory_type(fm.get("type"))
                if mem_type is None:
                    mem_type = "reference"  # graceful fallback

                stat = fp.stat()
                entries.append(
                    MemoryEntry(
                        name=fm.get("name", fp.stem),
                        description=fm.get("description", ""),
                        type=mem_type,
                        content=body,
                        created_at=datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc),
                        updated_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                        file_path=fp,
                    )
                )
            except Exception:
                continue  # skip unparsable files

        entries.sort(key=lambda e: e.updated_at, reverse=True)
        return entries[:_MAX_MEMORY_FILES]

    # ------------------------------------------------------------------ #
    #  Save
    # ------------------------------------------------------------------ #

    def save_memory(
        self,
        name: str,
        type: str,  # noqa: A002
        description: str,
        content: str,
    ) -> Path:
        """Write a memory .md file with YAML frontmatter + update MEMORY.md index.

        Returns the path of the new memory file.
        """
        if type not in MEMORY_TYPES:
            raise ValueError(f"Invalid memory type: {type!r}. Must be one of {MEMORY_TYPES}")

        self._ensure_dir()

        # Build slug-based filename, avoiding collisions
        slug = f"{type}_{_slugify(name)}"
        fp = self.memory_dir / f"{slug}.md"
        counter = 2
        while fp.exists():
            fp = self.memory_dir / f"{slug}_{counter}.md"
            counter += 1

        # Scrub any API keys / tokens from the body and description before
        # the memory is persisted to disk — memories live for weeks and may
        # be read back into prompts, so any secret here would leak.
        safe_description = scrub_secrets(description)
        safe_content = scrub_secrets(content)

        fm = {
            "name": name,
            "description": safe_description,
            "type": type,
        }
        fp.write_text(_render_frontmatter(fm, safe_content), encoding="utf-8")
        self._add_index_entry(name, fp.name, safe_description)
        return fp

    # ------------------------------------------------------------------ #
    #  Update
    # ------------------------------------------------------------------ #

    def update_memory(self, file_path: Path, content: str) -> None:
        """Update existing memory content, bump updated_at.

        Preserves the original frontmatter and replaces only the body.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Memory file not found: {file_path}")

        text = file_path.read_text(encoding="utf-8")
        fm, _old_body = _parse_frontmatter(text)
        # Scrub secrets before writing — same policy as save_memory().
        safe_content = scrub_secrets(content)
        file_path.write_text(_render_frontmatter(fm, safe_content), encoding="utf-8")
        # Touch to update mtime
        os.utime(file_path)

    # ------------------------------------------------------------------ #
    #  Delete
    # ------------------------------------------------------------------ #

    def delete_memory(self, file_path: Path) -> None:
        """Remove memory file + its MEMORY.md entry."""
        filename = file_path.name
        if file_path.exists():
            file_path.unlink()
        self._remove_index_entry(filename)

    # ------------------------------------------------------------------ #
    #  Search
    # ------------------------------------------------------------------ #

    def search(self, query: str) -> list[MemoryEntry]:
        """Simple keyword search across all memories.

        Matches against name, description, and content (case-insensitive).
        Results are ranked by number of keyword hits.
        """
        entries = self.load_all()
        query_lower = query.lower()
        keywords = query_lower.split()

        if not keywords:
            return entries

        scored: list[tuple[int, MemoryEntry]] = []
        for entry in entries:
            haystack = f"{entry.name} {entry.description} {entry.content}".lower()
            hits = sum(1 for kw in keywords if kw in haystack)
            if hits > 0:
                scored.append((hits, entry))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [entry for _, entry in scored]

    # ------------------------------------------------------------------ #
    #  Context for system prompt
    # ------------------------------------------------------------------ #

    def get_context_for_prompt(self, max_tokens: int = 2000) -> str:
        """Build the memory section for the system prompt.

        Returns MEMORY.md index + relevant memory bodies (newest first,
        trimmed to fit token budget).
        """
        # Start with the MEMORY.md index — this is a compact list of
        # one-line pointers like "- [Title](file.md) -- description"
        index = self.load_index()
        if not index:
            return ""

        parts: list[str] = [index.strip()]
        # Convert token budget to character budget using rough 4 chars/token
        char_budget = max_tokens * _TOKEN_CHARS
        used = len(parts[0])

        # Load all memory files, sorted newest-first so the most
        # recently updated memories are included first.
        entries = self.load_all()
        for entry in entries:
            # Build a compact block showing the memory type, name, age, and content.
            # Example: "### [feedback] Always use ruff (2 days ago)\nContent..."
            age = _memory_age_text(entry.updated_at.timestamp())
            block = f"\n### [{entry.type}] {entry.name} ({age})\n{entry.content}"

            # Append a staleness warning for memories older than 7 days.
            # This tells the model to verify claims before asserting them.
            staleness = _staleness_warning(entry.updated_at.timestamp())
            if staleness:
                block += f"\n> {staleness}"

            # Stop adding memories once we exceed the token budget.
            # The most important (newest) memories are already included.
            if used + len(block) > char_budget:
                break
            parts.append(block)
            used += len(block)

        return "\n".join(parts)

    # ------------------------------------------------------------------ #
    #  Staleness check
    # ------------------------------------------------------------------ #

    def check_staleness(self, entry: MemoryEntry) -> str | None:
        """Return staleness warning if memory is >7 days old."""
        return _staleness_warning(entry.updated_at.timestamp())
