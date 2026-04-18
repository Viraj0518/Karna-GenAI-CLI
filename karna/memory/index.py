"""MEMORY.md index -- one-line pointers into the typed memdir.

The index is the only memory artifact guaranteed to be injected into
every system prompt. Each line is intentionally short so a dozen
pointers fit in ~1 KB:

    # Memory Index

    - [User profile](user_profile.md) -- who Viraj is, his preferences
    - [Coding style](feedback_coding_style.md) -- terse commits, type hints

The bodies behind those links are only pulled into context when the
agent decides they are relevant (via :meth:`Memdir.search` or an
explicit tool call).

Paired with :class:`karna.memory.memdir.Memdir`. The index is fully
derivable from the memdir directory contents, so :meth:`rebuild_from_memdir`
can always regenerate it from scratch.
"""

from __future__ import annotations

from pathlib import Path

from karna.memory.memdir import Memdir

_INDEX_NAME = "MEMORY.md"
_HEADER = "# Memory Index\n"
_MAX_LINE_CHARS = 150


def _format_line(filename: str, name: str, description: str) -> str:
    """Render one index entry, clamped to ``_MAX_LINE_CHARS``."""
    name = name.strip() or filename
    description = description.strip()
    line = f"- [{name}]({filename}) -- {description}"
    if len(line) > _MAX_LINE_CHARS:
        line = line[: _MAX_LINE_CHARS - 3] + "..."
    return line


class MemoryIndex:
    """Maintains ``MEMORY.md`` alongside a :class:`Memdir`."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.home() / ".karna" / "memory"

    # ------------------------------------------------------------------ #
    #  Paths
    # ------------------------------------------------------------------ #

    @property
    def path(self) -> Path:
        return self.root / _INDEX_NAME

    def _ensure_dir(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    #  Reads
    # ------------------------------------------------------------------ #

    def read(self) -> str:
        """Return the MEMORY.md contents (empty string when missing)."""
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8")

    # ------------------------------------------------------------------ #
    #  Writes
    # ------------------------------------------------------------------ #

    def rebuild_from_memdir(self, memdir: Memdir) -> None:
        """Scan *memdir* and rewrite MEMORY.md from scratch."""
        self._ensure_dir()
        memories = memdir.list()
        lines: list[str] = [_HEADER, ""]
        for mem in memories:
            lines.append(_format_line(mem.filename, mem.name, mem.description))
        # Trailing newline for POSIX-friendliness
        content = "\n".join(lines).rstrip("\n") + "\n"
        self.path.write_text(content, encoding="utf-8")

    def add_entry(self, filename: str, description: str, name: str | None = None) -> None:
        """Append a single line (O(1) hot path).

        Silently dedupes: if *filename* is already referenced in the
        index, this is a no-op. The existing ``name`` (bracket text) is
        preserved when not passed explicitly.
        """
        self._ensure_dir()
        content = self.read()
        if not content:
            content = _HEADER + "\n"

        # Dedupe on filename
        if f"({filename})" in content:
            return

        display_name = name or filename.rsplit(".", 1)[0].replace("_", " ").title()
        new_line = _format_line(filename, display_name, description)
        content = content.rstrip("\n") + "\n" + new_line + "\n"
        self.path.write_text(content, encoding="utf-8")

    def remove_entry(self, filename: str) -> None:
        """Drop any line referencing *filename*."""
        content = self.read()
        if not content:
            return
        needle = f"({filename})"
        kept = [ln for ln in content.split("\n") if needle not in ln]
        # Collapse any accidental multiple blank lines
        out: list[str] = []
        blank = False
        for ln in kept:
            if ln.strip() == "":
                if blank:
                    continue
                blank = True
            else:
                blank = False
            out.append(ln)
        self.path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
