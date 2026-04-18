"""Git awareness — inject repo state into context.

Runs lightweight git commands to build a concise summary of the
repository state (branch, status, recent commits, uncommitted changes)
that is injected into the system context for every provider call.

Adapted from cc-src ``context.ts`` / ``utils/git.ts``.  See NOTICES.md.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_STATUS_CHARS = 2000


class GitContext:
    """Build git repo context strings."""

    def detect(self, cwd: Path) -> bool:
        """Return ``True`` if *cwd* is inside a git repository."""
        current = cwd.resolve()
        while True:
            if (current / ".git").exists():
                return True
            parent = current.parent
            if parent == current:
                return False
            current = parent

    async def get_context(self, cwd: Path) -> str | None:
        """Build a git context string for injection into the system prompt.

        Returns ``None`` if *cwd* is not inside a git repo or if git
        is not available.
        """
        if not self.detect(cwd):
            return None

        git_exe = shutil.which("git")
        if git_exe is None:
            return None

        try:
            repo_root, branch, status, log, diff_stat = await asyncio.gather(
                self._run_git("rev-parse", "--show-toplevel", cwd=cwd),
                self._run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd),
                self._run_git(
                    "--no-optional-locks",
                    "status",
                    "--short",
                    cwd=cwd,
                ),
                self._run_git(
                    "--no-optional-locks",
                    "log",
                    "--oneline",
                    "-n",
                    "5",
                    cwd=cwd,
                ),
                self._run_git(
                    "--no-optional-locks",
                    "diff",
                    "--stat",
                    "HEAD",
                    cwd=cwd,
                ),
            )
        except Exception:
            logger.debug("Git context collection failed", exc_info=True)
            return None

        # Truncate large status output.
        if len(status) > MAX_STATUS_CHARS:
            status = status[:MAX_STATUS_CHARS] + "\n... (truncated, run `git status` for full output)"

        # Parse status summary.
        status_summary = self._summarize_status(status)

        parts: list[str] = [
            f"Git repository: {repo_root.strip()}",
            f"Branch: {branch.strip()}",
        ]
        if status_summary:
            parts.append(f"Status: {status_summary}")
        else:
            parts.append("Status: clean")

        if log.strip():
            parts.append(f"Recent commits:\n{self._indent(log.strip())}")

        if status.strip():
            parts.append(f"Uncommitted changes:\n{self._indent(status.strip())}")

        if diff_stat.strip():
            parts.append(f"Diff stat:\n{self._indent(diff_stat.strip())}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    async def _run_git(self, *args: str, cwd: Path) -> str:
        """Run a git command and return stdout.  Returns ``""`` on error."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return ""
            return stdout.decode(errors="replace")
        except Exception:
            return ""

    @staticmethod
    def _summarize_status(status: str) -> str:
        """Produce a one-line summary like ``3 modified, 1 untracked``."""
        if not status.strip():
            return ""

        counts: dict[str, int] = {}
        for line in status.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            code = line[:2].strip()
            if code == "??":
                counts["untracked"] = counts.get("untracked", 0) + 1
            elif code.startswith("M") or code.endswith("M"):
                counts["modified"] = counts.get("modified", 0) + 1
            elif code.startswith("A") or code.endswith("A"):
                counts["added"] = counts.get("added", 0) + 1
            elif code.startswith("D") or code.endswith("D"):
                counts["deleted"] = counts.get("deleted", 0) + 1
            elif code.startswith("R"):
                counts["renamed"] = counts.get("renamed", 0) + 1
            else:
                counts["other"] = counts.get("other", 0) + 1

        parts = [f"{v} {k}" for k, v in counts.items()]
        return ", ".join(parts)

    @staticmethod
    def _indent(text: str, prefix: str = "  ") -> str:
        return "\n".join(f"{prefix}{line}" for line in text.splitlines())
