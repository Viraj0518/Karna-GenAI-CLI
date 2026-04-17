"""Git operations tool with safety guards.

Provides structured git operations for the Nellie agent with built-in
safety checks: blocks force-push, blocks reset --hard, warns on dirty
tree checkouts, and never auto-pushes.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from karna.tools.base import BaseTool

_TIMEOUT = 30  # seconds — git ops should be fast
_CO_AUTHOR = "Co-Authored-By: Nellie <noreply@karna.dev>"


async def _run(cmd: str, cwd: str) -> tuple[int, str]:
    """Run a git command and return (returncode, combined output)."""
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env={
            **os.environ,
            "TERM": "dumb",
            "GIT_TERMINAL_PROMPT": "0",
            # Ensure git identity is always available (fallback for machines
            # without global git config — e.g., CI, containers, cloud VMs).
            "GIT_AUTHOR_NAME": os.environ.get("GIT_AUTHOR_NAME", os.environ.get("USER", "Nellie")),
            "GIT_COMMITTER_NAME": os.environ.get("GIT_COMMITTER_NAME", os.environ.get("USER", "Nellie")),
            "GIT_AUTHOR_EMAIL": os.environ.get("GIT_AUTHOR_EMAIL", "nellie@karna.dev"),
            "GIT_COMMITTER_EMAIL": os.environ.get("GIT_COMMITTER_EMAIL", "nellie@karna.dev"),
        },
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_TIMEOUT
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 1, f"[error] git command timed out after {_TIMEOUT}s"

    out = stdout.decode("utf-8", errors="replace").rstrip()
    err = stderr.decode("utf-8", errors="replace").rstrip()
    combined = "\n".join(filter(None, [out, err]))
    return proc.returncode or 0, combined


class GitTool(BaseTool):
    """Perform git operations with safety checks.

    Prefer this over running git via bash — it enforces guardrails that
    prevent accidental data loss (force-push, reset --hard, etc.).
    """

    name = "git"
    description = (
        "Perform git operations with safety checks. "
        "Prefer this over running git via bash."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "status",
                    "diff",
                    "log",
                    "add",
                    "commit",
                    "branch",
                    "stash",
                    "checkout",
                ],
                "description": "Git action to perform",
            },
            "args": {
                "type": "string",
                "description": "Additional arguments for the git command",
            },
            "message": {
                "type": "string",
                "description": "Commit message (for commit action)",
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Files to add/stage (for add action)",
            },
        },
        "required": ["action"],
    }

    def __init__(self, *, cwd: str | None = None) -> None:
        super().__init__()
        self._cwd = cwd or os.getcwd()

    # ------------------------------------------------------------------ #
    #  Safety checks
    # ------------------------------------------------------------------ #

    @staticmethod
    def _check_safety(action: str, args: str) -> str | None:
        """Return an error message if the operation is unsafe, else None."""
        combined = f"{action} {args}".lower()

        # Block force-push
        if "push" in combined and ("--force" in combined or " -f" in combined):
            return (
                "[BLOCKED] Force-push is not allowed. "
                "Use a regular push or rebase instead."
            )

        # Block reset --hard
        if "reset" in combined and "--hard" in combined:
            return (
                "[BLOCKED] git reset --hard is destructive and not allowed. "
                "Use git stash or git checkout <file> instead."
            )

        return None

    # ------------------------------------------------------------------ #
    #  Action handlers
    # ------------------------------------------------------------------ #

    async def _status(self, args: str) -> str:
        """git status --short + branch + ahead/behind."""
        _, branch = await _run("git branch --show-current", self._cwd)
        _, tracking = await _run(
            "git rev-list --left-right --count @{upstream}...HEAD 2>/dev/null",
            self._cwd,
        )
        _, status = await _run(f"git status --short {args}".strip(), self._cwd)

        parts = [f"Branch: {branch or '(detached HEAD)'}"]

        if tracking and "\t" in tracking:
            behind, ahead = tracking.split("\t")
            behind, ahead = behind.strip(), ahead.strip()
            if ahead != "0" or behind != "0":
                parts.append(f"Ahead: {ahead}, Behind: {behind}")

        parts.append(status or "(clean working tree)")
        return "\n".join(parts)

    async def _diff(self, args: str) -> str:
        """git diff — auto-detect staged vs unstaged."""
        # If caller passed explicit args, honour them.
        if args:
            _, out = await _run(f"git diff {args}", self._cwd)
            return out or "(no diff)"

        # Otherwise: show unstaged, and if nothing, show staged.
        _, unstaged = await _run("git diff", self._cwd)
        if unstaged.strip():
            return unstaged

        _, staged = await _run("git diff --cached", self._cwd)
        if staged.strip():
            return f"(showing staged changes)\n{staged}"

        return "(no changes)"

    async def _log(self, args: str) -> str:
        """git log --oneline with author and relative dates."""
        extra = args or "-10"
        _, out = await _run(
            f"git log --oneline --format='%h %<(15,trunc)%an %ar  %s' {extra}",
            self._cwd,
        )
        return out or "(no commits)"

    async def _add(self, args: str, files: list[str] | None) -> str:
        """Stage specific files. Refuses git add -A unless explicit."""
        if not files and not args:
            return (
                "[error] No files specified. "
                "Provide a list of files to stage, or pass args='-A' explicitly."
            )

        if files:
            # Quote each path
            quoted = " ".join(f'"{f}"' for f in files)
            rc, out = await _run(f"git add {quoted}", self._cwd)
        else:
            rc, out = await _run(f"git add {args}", self._cwd)

        if rc != 0:
            return f"[error] git add failed:\n{out}"

        # Show what was staged
        _, staged = await _run("git diff --cached --stat", self._cwd)
        return f"Staged.\n{staged}" if staged else "Staged (no diff summary available)."

    async def _commit(self, args: str, message: str) -> str:
        """Commit with message validation and Co-Authored-By trailer."""
        if "--amend" in args:
            return (
                "[BLOCKED] --amend is not allowed by default. "
                "If you really need to amend, use the bash tool directly."
            )

        if not message:
            return "[error] Commit message is required."

        # Append co-author trailer
        full_msg = f"{message}\n\n{_CO_AUTHOR}"

        rc, out = await _run(
            f'git commit -m "{full_msg.replace(chr(34), chr(39))}" {args}'.strip(),
            self._cwd,
        )
        if rc != 0:
            return f"[error] Commit failed:\n{out}"

        return out

    async def _branch(self, args: str) -> str:
        """List, create, or switch branches."""
        if not args:
            # List branches
            _, out = await _run("git branch -vv", self._cwd)
            return out or "(no branches)"

        # If args starts with a known flag, pass through
        # Otherwise treat it as "create and switch"
        if args.startswith("-"):
            _, out = await _run(f"git branch {args}", self._cwd)
            return out or "(done)"

        # Check for dirty tree before switching
        _, status = await _run("git status --porcelain", self._cwd)
        warning = ""
        if status.strip():
            warning = (
                "[warning] Working tree has uncommitted changes. "
                "Consider committing or stashing first.\n"
            )

        # Try create-and-switch first; if branch exists, just switch
        rc, out = await _run(f"git checkout -b {args}", self._cwd)
        if rc != 0 and "already exists" in out:
            rc, out = await _run(f"git checkout {args}", self._cwd)
        if rc != 0:
            return f"{warning}[error] Branch operation failed:\n{out}"
        return f"{warning}{out}"

    async def _stash(self, args: str) -> str:
        """Stash push/pop/list."""
        sub = args or "list"
        rc, out = await _run(f"git stash {sub}", self._cwd)
        if rc != 0:
            return f"[error] git stash {sub} failed:\n{out}"
        return out or "(stash operation completed)"

    async def _checkout(self, args: str) -> str:
        """Switch branches only — not file checkout."""
        if not args:
            return "[error] Branch name required for checkout."

        # Block file-checkout patterns (paths with extensions or explicit --)
        if "--" in args and args.index("--") < len(args) - 2:
            return (
                "[BLOCKED] File checkout is not supported via this tool — "
                "it is too dangerous. Use git stash or manual revert instead."
            )

        # Dirty tree warning
        _, status = await _run("git status --porcelain", self._cwd)
        warning = ""
        if status.strip():
            warning = (
                "[warning] Working tree has uncommitted changes. "
                "Consider committing or stashing first.\n"
            )

        rc, out = await _run(f"git checkout {args}", self._cwd)
        if rc != 0:
            return f"{warning}[error] Checkout failed:\n{out}"
        return f"{warning}{out}"

    # ------------------------------------------------------------------ #
    #  Dispatcher
    # ------------------------------------------------------------------ #

    async def execute(self, **kwargs: Any) -> str:
        action: str = kwargs["action"]
        args: str = kwargs.get("args", "")
        message: str = kwargs.get("message", "")
        files: list[str] | None = kwargs.get("files")

        # Global safety check on the full args string
        blocked = self._check_safety(action, args)
        if blocked:
            return blocked

        handlers = {
            "status": lambda: self._status(args),
            "diff": lambda: self._diff(args),
            "log": lambda: self._log(args),
            "add": lambda: self._add(args, files),
            "commit": lambda: self._commit(args, message),
            "branch": lambda: self._branch(args),
            "stash": lambda: self._stash(args),
            "checkout": lambda: self._checkout(args),
        }

        handler = handlers.get(action)
        if not handler:
            return f"[error] Unknown action: {action}"

        try:
            return await handler()
        except Exception as exc:
            return f"[error] {exc}"
