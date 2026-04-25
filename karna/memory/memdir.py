"""Typed Memdir -- upstream reference-style persistent memory directory.

Complementary to :mod:`karna.memory.manager` but exposes a more
Claude-Code-faithful API surface:

- Every memory is a ``.md`` file with YAML frontmatter on disk.
- Frontmatter carries ``name``, ``description``, ``type``,
  ``created`` and ``last_updated`` ISO-8601 timestamps.
- Four canonical types only: ``user``, ``feedback``, ``project``,
  ``reference``.
- Secrets are scrubbed on every write via
  :func:`karna.security.guards.scrub_secrets`.

Layout::

    ~/.karna/memory/
        MEMORY.md                    <- index (maintained by MemoryIndex)
        user_profile.md              <- type=user
        feedback_coding_style.md     <- type=feedback
        project_karna_context.md     <- type=project
        reference_apis.md            <- type=reference
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from karna.memory.types import MEMORY_TYPES, MemoryType, parse_memory_type
from karna.security.guards import scrub_secrets

_INDEX_NAME = "MEMORY.md"
_FM_FENCE = re.compile(r"^---\s*$")


# --------------------------------------------------------------------------- #
#  Data class
# --------------------------------------------------------------------------- #


@dataclass
class Memory:
    """A single memdir memory record."""

    filename: str
    type: MemoryType
    name: str
    description: str
    body: str
    created: datetime
    last_updated: datetime


# --------------------------------------------------------------------------- #
#  Frontmatter helpers
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(raw: str | None) -> datetime:
    """Parse an ISO-8601 timestamp, falling back to now() if malformed."""
    if not raw:
        return datetime.now(timezone.utc)
    try:
        # Accept trailing Z
        cleaned = raw.rstrip("Z")
        if "+" not in cleaned and "T" in cleaned:
            return datetime.fromisoformat(cleaned).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return datetime.now(timezone.utc)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML-lite frontmatter: simple ``key: value`` lines."""
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
            val = val.strip().strip('"').strip("'")
            fm[key.strip()] = val

    body = "\n".join(lines[end_idx + 1 :]).strip()
    return fm, body


def _render_frontmatter(fm: dict[str, str], body: str) -> str:
    parts = ["---"]
    for k, v in fm.items():
        parts.append(f'{k}: "{v}"')
    parts.append("---")
    parts.append("")
    parts.append(body)
    return "\n".join(parts) + "\n"


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower().strip()).strip("_")
    return slug[:60] or "memory"


# --------------------------------------------------------------------------- #
#  Memdir
# --------------------------------------------------------------------------- #


class Memdir:
    """A typed memory directory.

    All mutation paths scrub secrets before writing. Reads are
    frontmatter-aware -- files without valid frontmatter degrade to
    ``type=reference`` with sensible defaults.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.home() / ".karna" / "memory"

    # ------------------------------------------------------------------ #
    #  Mutations
    # ------------------------------------------------------------------ #

    def _ensure_dir(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def add(
        self,
        *,
        type: MemoryType,  # noqa: A002
        name: str,
        description: str,
        body: str,
    ) -> str:
        """Create a new memory file and return its filename."""
        if type not in MEMORY_TYPES:
            raise ValueError(f"Invalid memory type: {type!r}. Must be one of {MEMORY_TYPES}")

        self._ensure_dir()

        slug = f"{type}_{_slugify(name)}"
        path = self.root / f"{slug}.md"
        counter = 2
        while path.exists():
            path = self.root / f"{slug}_{counter}.md"
            counter += 1

        now = _now_iso()
        fm = {
            "name": scrub_secrets(name),
            "description": scrub_secrets(description),
            "type": type,
            "created": now,
            "last_updated": now,
        }
        path.write_text(_render_frontmatter(fm, scrub_secrets(body)), encoding="utf-8")
        return path.name

    def update(self, filename: str, body: str) -> None:
        """Replace the body of an existing memory, bumping ``last_updated``."""
        path = self.root / filename
        if not path.exists():
            raise FileNotFoundError(f"Memory file not found: {filename}")

        text = path.read_text(encoding="utf-8")
        fm, _ = _parse_frontmatter(text)
        fm["last_updated"] = _now_iso()
        # preserve created if present, else backfill
        fm.setdefault("created", fm["last_updated"])
        path.write_text(_render_frontmatter(fm, scrub_secrets(body)), encoding="utf-8")

    def delete(self, filename: str) -> None:
        path = self.root / filename
        if path.exists():
            path.unlink()

    # ------------------------------------------------------------------ #
    #  Reads
    # ------------------------------------------------------------------ #

    def get(self, filename: str) -> Memory:
        path = self.root / filename
        if not path.exists():
            raise FileNotFoundError(f"Memory file not found: {filename}")
        return self._load(path)

    def list(self, *, type: MemoryType | None = None) -> list[Memory]:  # noqa: A002
        if not self.root.exists():
            return []
        memories: list[Memory] = []
        for fp in self.root.glob("*.md"):
            if fp.name == _INDEX_NAME:
                continue
            try:
                mem = self._load(fp)
            except Exception:
                continue
            if type is not None and mem.type != type:
                continue
            memories.append(mem)
        memories.sort(key=lambda m: m.last_updated, reverse=True)
        return memories

    def search(self, query: str, *, limit: int = 10) -> list[Memory]:
        """Simple keyword/substring search. No LLM involvement."""
        q = query.lower().strip()
        if not q:
            return self.list()[:limit]
        keywords = q.split()

        scored: list[tuple[int, Memory]] = []
        for mem in self.list():
            haystack = f"{mem.name} {mem.description} {mem.body}".lower()
            # Substring match: full query as single phrase
            score = 2 if q in haystack else 0
            # Plus per-keyword hits
            score += sum(1 for kw in keywords if kw in haystack)
            if score > 0:
                scored.append((score, mem))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [m for _, m in scored[:limit]]

    # ------------------------------------------------------------------ #
    #  Internal
    # ------------------------------------------------------------------ #

    def _load(self, path: Path) -> Memory:
        text = path.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)
        mem_type = parse_memory_type(fm.get("type")) or "reference"
        stat = path.stat()
        created_raw = fm.get("created")
        updated_raw = fm.get("last_updated")
        created = _parse_iso(created_raw) if created_raw else datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc)
        updated = _parse_iso(updated_raw) if updated_raw else datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        return Memory(
            filename=path.name,
            type=mem_type,
            name=fm.get("name", path.stem),
            description=fm.get("description", ""),
            body=body,
            created=created,
            last_updated=updated,
        )
