"""Glob tool — file pattern matching.

Full implementation ported from cc-src GlobTool with attribution to
the Anthropic Claude Code codebase.

Features:
- File pattern matching via ``pathlib.Path.glob()``
- Results sorted by modification time (most recent first)
- Respects ``.gitignore`` via ``git ls-files`` when available
- Configurable result limit (default 100)
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any

from karna.tools.base import BaseTool

_DEFAULT_LIMIT = 100


class GlobTool(BaseTool):
    """Find files matching glob patterns, sorted by modification time."""

    name = "glob"
    description = (
        "Find files matching a glob pattern. "
        "Results sorted by modification time (most recent first)."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": 'Glob pattern (e.g. "**/*.py").',
            },
            "path": {
                "type": "string",
                "description": (
                    "Root directory to search from. "
                    "Defaults to current working directory."
                ),
            },
        },
        "required": ["pattern"],
    }

    async def execute(self, **kwargs: Any) -> str:
        pattern: str = kwargs["pattern"]
        root: str = kwargs.get("path", os.getcwd())
        root = os.path.expanduser(root)

        root_path = Path(root)
        if not root_path.is_dir():
            return f"[error] Directory does not exist: {root}"

        # Try git-aware listing first (respects .gitignore)
        git_files = await self._git_ls_files(root_path, pattern)

        if git_files is not None:
            files = git_files
        else:
            # Fallback: plain pathlib glob
            try:
                files = [
                    str(p)
                    for p in root_path.glob(pattern)
                    if p.is_file()
                ]
            except Exception as exc:
                return f"[error] {exc}"

        # Sort by modification time (most recent first)
        def _mtime(path_str: str) -> float:
            try:
                return os.path.getmtime(path_str)
            except OSError:
                return 0.0

        files.sort(key=_mtime, reverse=True)

        truncated = len(files) > _DEFAULT_LIMIT
        files = files[:_DEFAULT_LIMIT]

        if not files:
            return "No files found"

        result = "\n".join(files)
        if truncated:
            result += (
                "\n(Results are truncated. "
                "Consider using a more specific path or pattern.)"
            )

        return result

    # ------------------------------------------------------------------ #
    #  git-aware file listing
    # ------------------------------------------------------------------ #

    @staticmethod
    async def _git_ls_files(
        root: Path, pattern: str
    ) -> list[str] | None:
        """Use ``git ls-files`` filtered by *pattern* if inside a repo.

        Returns ``None`` if not in a git repo or git is not available.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "ls-files", "--cached", "--others",
                "--exclude-standard", root.as_posix(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(root),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return None

            all_files = stdout.decode("utf-8", errors="replace").strip().splitlines()

            # Filter against the glob pattern using PurePosixPath.match
            from fnmatch import fnmatch

            matched = []
            for f in all_files:
                full = root / f
                if full.is_file() and fnmatch(f, pattern):
                    matched.append(str(full))

            return matched

        except (asyncio.TimeoutError, FileNotFoundError, OSError):
            return None
