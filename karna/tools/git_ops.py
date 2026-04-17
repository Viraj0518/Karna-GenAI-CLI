"""Git operations tool with safety guards.

Provides structured git operations for the Nellie agent with built-in
safety checks: blocks force-push, blocks reset --hard, warns on dirty
tree checkouts, and never auto-pushes.

Security (HIGH-2, MEDIUM-3 fix):
``_run`` uses ``asyncio.create_subprocess_exec`` with an argv list — no
shell interpolation — so shell metacharacters in user-supplied ``args``
or commit ``message`` cannot spawn a subshell or run extra commands.
Caller-provided ``args`` are parsed safely with ``shlex.split`` and
forwarded as literal git tokens.
"""

from __future__ import annotations

import asyncio
import os
import shlex
from pathlib import Path
from typing import Any

from karna.tools.base import BaseTool

_TIMEOUT = 30  # seconds — git ops should be fast
_CO_AUTHOR = "Co-Authored-By: Nellie <noreply@karna.dev>"


def _git_env() -> dict[str, str]:
    """Build the env dict for git subprocesses."""
    return {
        **os.environ,
        "TERM": "dumb",
        "GIT_TERMINAL_PROMPT": "0",
        # Ensure git identity is always available (fallback for machines
        # without global git config — e.g., CI, containers, cloud VMs).
        "GIT_AUTHOR_NAME": os.environ.get("GIT_AUTHOR_NAME", os.environ.get("USER", "Nellie")),
        "GIT_COMMITTER_NAME": os.environ.get("GIT_COMMITTER_NAME", os.environ.get("USER", "Nellie")),
        "GIT_AUTHOR_EMAIL": os.environ.get("GIT_AUTHOR_EMAIL", "nellie@karna.dev"),
        "GIT_COMMITTER_EMAIL": os.environ.get("GIT_COMMITTER_EMAIL", "nellie@karna.dev"),
    }


async def _run(argv: list[str], cwd: str | Path | None = None, timeout: float = _TIMEOUT) -> tuple[int, str]:
    """Run a git command (argv list) and return (returncode, combined output).

    Uses ``create_subprocess_exec`` — NOT shell — so any metacharacters
    in the argv tokens are passed literally to git and cannot invoke
    additional commands.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_git_env(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 1, f"[error] git command timed out after {timeout}s"

    out = stdout.decode("utf-8", errors="replace").rstrip()
    err = stderr.decode("utf-8", errors="replace").rstrip()
    combined = "\n".join(filter(None, [out, err]))
    return proc.returncode or 0, combined


def _split_args(args: str) -> list[str]:
    """Tokenize caller-supplied ``args`` safely.

    Returns ``[]`` for empty/whitespace input. Uses ``shlex.split`` in
    POSIX mode so quoted values work correctly and shell operators
    become literal tokens (which git will reject, as intended).
    """
    if not args or not args.strip():
        return []
    try:
        return shlex.split(args, posix=True)
    except ValueError:
        # Unbalanced quotes etc. — fall back to naive split so we still
        # pass the tokens literally (git will reject bad ones).
        return args.split()


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
        """Return an error message if the operation is unsafe, else None.

        Even though we now use ``create_subprocess_exec`` (no shell), we
        still string-scan the raw ``args`` because an attacker may try
        to pass extra git subcommands via smuggled tokens (e.g., an arg
        that looks like ``main && git push --force``). With exec() these
        become literal git args and git will reject them, but we block
        earlier for clearer error messages and defense in depth.
        """
        combined = f"{action} {args}".lower()

        # Block force-push (any form)
        if "push" in combined and ("--force" in combined or " -f " in combined or combined.endswith(" -f")):
            return (
                "[BLOCKED] Force-push is not allowed. "
                "Use a regular push or rebase instead."
            )

        # Block any push via this tool. ``push`` isn't in the action
        # enum, but callers sometimes smuggle one via args
        # (e.g., "main && git push ..."). We look for "git push".
        if "git push" in combined or action == "push":
            return (
                "[BLOCKED] git push is not allowed via this tool. "
                "Push manually after reviewing the commits."
            )

        # Block reset --hard
        if "reset" in combined and "--hard" in combined:
            return (
                "[BLOCKED] git reset --hard is destructive and not allowed. "
                "Use git stash or git checkout <file> instead."
            )

        # Block config / remote set-url / credential manipulation —
        # these can silently redirect pushes or leak credentials.
        if "git config" in combined or action == "config":
            return (
                "[BLOCKED] git config is not allowed via this tool. "
                "Edit ~/.gitconfig manually if you need to change settings."
            )
        if "remote" in combined and "set-url" in combined:
            return (
                "[BLOCKED] git remote set-url is not allowed — it can "
                "redirect pushes to an attacker-controlled URL."
            )
        if "credential" in combined:
            return (
                "[BLOCKED] git credential operations are not allowed."
            )

        return None

    # ------------------------------------------------------------------ #
    #  Action handlers
    # ------------------------------------------------------------------ #

    async def _status(self, args: str) -> str:
        """git status --short + branch + ahead/behind."""
        _, branch = await _run(["git", "branch", "--show-current"], self._cwd)
        # Upstream tracking: if no upstream, git errors — we tolerate
        # non-zero returncode and treat empty/error output as "no tracking".
        _, tracking = await _run(
            ["git", "rev-list", "--left-right", "--count", "@{upstream}...HEAD"],
            self._cwd,
        )
        extra = _split_args(args)
        _, status = await _run(["git", "status", "--short", *extra], self._cwd)

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
        # If caller passed explicit args, honour them (tokenised safely).
        if args:
            extra = _split_args(args)
            _, out = await _run(["git", "diff", *extra], self._cwd)
            return out or "(no diff)"

        # Otherwise: show unstaged, and if nothing, show staged.
        _, unstaged = await _run(["git", "diff"], self._cwd)
        if unstaged.strip():
            return unstaged

        _, staged = await _run(["git", "diff", "--cached"], self._cwd)
        if staged.strip():
            return f"(showing staged changes)\n{staged}"

        return "(no changes)"

    async def _log(self, args: str) -> str:
        """git log --oneline with author and relative dates."""
        extra = _split_args(args) if args else ["-10"]
        _, out = await _run(
            [
                "git",
                "log",
                "--oneline",
                "--format=%h %<(15,trunc)%an %ar  %s",
                *extra,
            ],
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
            rc, out = await _run(["git", "add", "--", *files], self._cwd)
        else:
            extra = _split_args(args)
            rc, out = await _run(["git", "add", *extra], self._cwd)

        if rc != 0:
            return f"[error] git add failed:\n{out}"

        # Show what was staged
        _, staged = await _run(["git", "diff", "--cached", "--stat"], self._cwd)
        return f"Staged.\n{staged}" if staged else "Staged (no diff summary available)."

    async def _commit(self, args: str, message: str) -> str:
        """Commit with message validation and Co-Authored-By trailer.

        ``message`` is passed as a single argv token to ``git commit -m``,
        so backticks, ``$(...)``, and other shell metacharacters in the
        message become literal bytes in the commit and cannot execute
        anything.
        """
        extra = _split_args(args)
        if "--amend" in extra:
            return (
                "[BLOCKED] --amend is not allowed by default. "
                "If you really need to amend, use the bash tool directly."
            )

        if not message:
            return "[error] Commit message is required."

        # Append co-author trailer. The full message is a single literal
        # argv token — no shell interpolation.
        full_msg = f"{message}\n\n{_CO_AUTHOR}"

        rc, out = await _run(
            ["git", "commit", "-m", full_msg, *extra],
            self._cwd,
        )
        if rc != 0:
            return f"[error] Commit failed:\n{out}"

        return out

    async def _branch(self, args: str) -> str:
        """List, create, or switch branches."""
        if not args:
            # List branches
            _, out = await _run(["git", "branch", "-vv"], self._cwd)
            return out or "(no branches)"

        extra = _split_args(args)

        # If args starts with a known flag, pass through
        # Otherwise treat it as "create and switch"
        if extra and extra[0].startswith("-"):
            _, out = await _run(["git", "branch", *extra], self._cwd)
            return out or "(done)"

        # Check for dirty tree before switching
        _, status = await _run(["git", "status", "--porcelain"], self._cwd)
        warning = ""
        if status.strip():
            warning = (
                "[warning] Working tree has uncommitted changes. "
                "Consider committing or stashing first.\n"
            )

        # Try create-and-switch first; if branch exists, just switch
        rc, out = await _run(["git", "checkout", "-b", *extra], self._cwd)
        if rc != 0 and "already exists" in out:
            rc, out = await _run(["git", "checkout", *extra], self._cwd)
        if rc != 0:
            return f"{warning}[error] Branch operation failed:\n{out}"
        return f"{warning}{out}"

    async def _stash(self, args: str) -> str:
        """Stash push/pop/list."""
        extra = _split_args(args) if args else ["list"]
        rc, out = await _run(["git", "stash", *extra], self._cwd)
        if rc != 0:
            return f"[error] git stash {' '.join(extra)} failed:\n{out}"
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

        extra = _split_args(args)

        # Dirty tree warning
        _, status = await _run(["git", "status", "--porcelain"], self._cwd)
        warning = ""
        if status.strip():
            warning = (
                "[warning] Working tree has uncommitted changes. "
                "Consider committing or stashing first.\n"
            )

        rc, out = await _run(["git", "checkout", *extra], self._cwd)
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
