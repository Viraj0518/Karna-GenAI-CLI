"""Project context detection — reads KARNA.md, CLAUDE.md, and friends.

Walks up from the working directory looking for project-specific
instruction files.  Supports multiple conventions so Karna works in
any project that already has AI-assistant configuration.

Adapted from cc-src ``utils/claudemd.ts``.  See NOTICES.md.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

# Priority order (highest first).  All matching files are loaded and
# concatenated so that Karna works in a project that already has
# CLAUDE.md *and* gains KARNA.md-specific overrides.
_SEARCH_FILES: list[tuple[str, str]] = [
    # (relative_path, label)
    ("KARNA.md", "karna project instructions"),
    ("CLAUDE.md", "project instructions (Claude Code compatible)"),
    (".karna/project.toml", "karna project config"),
    (".cursorrules", "project instructions (Cursor compatible)"),
    (".github/copilot-instructions.md", "project instructions (Copilot compatible)"),
]


class ProjectContext:
    """Detect and load project-level instruction files."""

    def detect(self, cwd: Path) -> str | None:
        """Walk up from *cwd* looking for project context files.

        Returns combined content from all discovered files (closest to
        *cwd* wins when a filename appears at multiple levels), or
        ``None`` if nothing was found.
        """
        collected: list[str] = []
        # Track which filenames we've already seen so a closer copy
        # shadows one further up the tree.
        seen_filenames: set[str] = set()

        # Walk up from cwd to filesystem root.
        dirs: list[Path] = []
        current = cwd.resolve()
        while True:
            dirs.append(current)
            parent = current.parent
            if parent == current:
                break
            current = parent

        # Process closest-first so shadowing works correctly.
        for d in dirs:
            for rel_path, label in _SEARCH_FILES:
                if rel_path in seen_filenames:
                    continue
                candidate = d / rel_path
                if not candidate.is_file():
                    continue
                seen_filenames.add(rel_path)
                try:
                    if rel_path.endswith(".toml"):
                        content = self.load_project_toml(candidate)
                    else:
                        content = self.load_karna_md(candidate)
                    if content.strip():
                        header = f"# {label} ({candidate})"
                        collected.append(f"{header}\n\n{content.strip()}")
                        logger.debug("Loaded project context: %s", candidate)
                except Exception:
                    logger.warning("Failed to read project context: %s", candidate, exc_info=True)

        if not collected:
            return None
        return "\n\n---\n\n".join(collected)

    # ------------------------------------------------------------------
    #  File loaders
    # ------------------------------------------------------------------

    def load_karna_md(self, path: Path) -> str:
        """Read a markdown instruction file (KARNA.md, CLAUDE.md, etc.)."""
        return path.read_text(encoding="utf-8", errors="replace")

    def load_project_toml(self, path: Path) -> str:
        """Read ``.karna/project.toml`` and format as human-readable context."""
        raw = path.read_bytes()
        data: dict[str, Any] = tomllib.loads(raw.decode(errors="replace"))

        parts: list[str] = []
        if "instructions" in data:
            parts.append(str(data["instructions"]))
        if "rules" in data:
            rules = data["rules"]
            if isinstance(rules, list):
                parts.append("Rules:\n" + "\n".join(f"- {r}" for r in rules))
            elif isinstance(rules, str):
                parts.append(f"Rules: {rules}")
        # Anything else — just dump key=value.
        for key, value in data.items():
            if key in ("instructions", "rules"):
                continue
            parts.append(f"{key}: {value}")

        return "\n\n".join(parts)
