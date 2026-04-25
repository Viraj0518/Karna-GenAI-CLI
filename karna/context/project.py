"""Project context detection — reads KARNA.md, CLAUDE.md, and friends.

Walks up from the working directory looking for project-specific
instruction files.  Supports multiple conventions so Karna works in
any project that already has AI-assistant configuration.

**Priority system (E8 — hierarchical merge)**:

1. ``{project_root}/KARNA.md``       — highest priority
2. ``{project_root}/.karna/KARNA.md`` — project-level alternate location
3. ``~/.karna/KARNA.md``             — global default

Global KARNA.md is used only if no project-level KARNA.md is found.
CLAUDE.md and .cursorrules are loaded with lower priority for compatibility.

Adapted from upstream ``utils/claudemd.ts``.  See NOTICES.md.
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
_SEARCH_FILES: list[tuple[str, str, int]] = [
    # (relative_path, label, priority)  — lower number = higher priority
    ("KARNA.md", "karna project instructions", 1),
    (".karna/KARNA.md", "karna project instructions (.karna/)", 2),
    ("CLAUDE.md", "project instructions (upstream reference compatible)", 5),
    (".karna/project.toml", "karna project config", 6),
    (".cursorrules", "project instructions (Cursor compatible)", 7),
    (".github/copilot-instructions.md", "project instructions (Copilot compatible)", 8),
]

# Global KARNA.md location — always checked as a fallback
_GLOBAL_KARNA_MD = Path.home() / ".karna" / "KARNA.md"

# The global ~/.karna/ directory — files under this path should NOT be
# picked up by the ancestor walk (they are loaded separately as a fallback).
_GLOBAL_KARNA_DIR = Path.home() / ".karna"


class ProjectContext:
    """Detect and load project-level instruction files."""

    def detect(self, cwd: Path) -> str | None:
        """Walk up from *cwd* looking for project context files.

        Implements the hierarchical merge strategy (E8):
        1. Project-level files (closest to cwd win)
        2. Global ``~/.karna/KARNA.md`` as fallback (priority 3)
        3. Compatibility files (CLAUDE.md, .cursorrules) at lower priority

        Files under ``~/.karna/`` are never treated as project-level
        context — they are loaded exclusively via the global fallback
        path below.  This prevents ``~/.karna/KARNA.md`` from being
        mis-detected as a project file when the ancestor walk reaches
        ``$HOME``.

        Returns combined content from all discovered files (closest to
        *cwd* wins when a filename appears at multiple levels), or
        ``None`` if nothing was found.
        """
        # Collect as (content, priority, label) tuples, sort by priority
        collected: list[tuple[str, int, str]] = []
        # Track which filenames we've already seen so a closer copy
        # shadows one further up the tree.
        seen_filenames: set[str] = set()

        # Resolve the global karna dir once for comparison.
        global_karna_dir = _GLOBAL_KARNA_DIR.resolve()

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
            for rel_path, label, priority in _SEARCH_FILES:
                if rel_path in seen_filenames:
                    continue
                candidate = d / rel_path
                if not candidate.is_file():
                    continue
                # Skip files that live under ~/.karna/ — those are
                # global, not project-level, and are loaded separately.
                try:
                    resolved = candidate.resolve()
                    if resolved == global_karna_dir / resolved.name or str(resolved).startswith(
                        str(global_karna_dir) + "/"
                    ):
                        continue
                except OSError:
                    pass
                seen_filenames.add(rel_path)
                try:
                    if rel_path.endswith(".toml"):
                        content = self.load_project_toml(candidate)
                    else:
                        content = self.load_karna_md(candidate)
                    if content.strip():
                        header = f"# {label} ({candidate})"
                        collected.append((f"{header}\n\n{content.strip()}", priority, label))
                        logger.debug("Loaded project context: %s (priority %d)", candidate, priority)
                except Exception:
                    logger.warning("Failed to read project context: %s", candidate, exc_info=True)

        # Global KARNA.md — only if no project-level KARNA.md was found
        has_project_karna = "KARNA.md" in seen_filenames or ".karna/KARNA.md" in seen_filenames
        if not has_project_karna:
            try:
                global_path = _GLOBAL_KARNA_MD
                if global_path.is_file():
                    content = self.load_karna_md(global_path)
                    if content.strip():
                        header = f"# karna global instructions ({global_path})"
                        collected.append((f"{header}\n\n{content.strip()}", 3, "global karna"))
                        logger.debug("Loaded global KARNA.md: %s", global_path)
            except Exception:
                logger.warning("Failed to read global KARNA.md", exc_info=True)

        if not collected:
            return None

        # Sort by priority (lower number = higher priority, injected first)
        collected.sort(key=lambda t: t[1])
        return "\n\n---\n\n".join(c[0] for c in collected)

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
