"""Bash tool — executes shell commands via asyncio subprocess.

Full implementation ported from cc-src BashTool with attribution to
the Anthropic Claude Code codebase.

Features:
- Async subprocess execution via ``asyncio.create_subprocess_shell``
- stdout + stderr capture
- Configurable timeout (default 120 s)
- Working directory tracking
- Output truncation for large results
- Basic dangerous-command validation
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from karna.tools.base import BaseTool

_DEFAULT_TIMEOUT = 120  # seconds
_MAX_OUTPUT_CHARS = 100_000  # truncate beyond this


# ----------------------------------------------------------------------- #
#  Dangerous-command patterns (ported from cc-src bashSecurity.ts)
# ----------------------------------------------------------------------- #

_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+(-[rR]f?|--recursive)\s+/\s*$"), "recursive delete of root filesystem"),
    (re.compile(r"\brm\s+(-[rR]f?|--recursive)\s+/\s"), "recursive delete of root filesystem"),
    (re.compile(r"\bdd\b.*\bof=/dev/[sh]d"), "direct write to block device"),
    (re.compile(r"\bmkfs\b"), "filesystem format command"),
    (re.compile(r"\b:(){ :\|:& };:"), "fork bomb"),
    (re.compile(r">\s*/dev/[sh]d"), "redirect to block device"),
    (re.compile(r"\bchmod\s+-R\s+777\s+/\s*$"), "recursive 777 chmod of root"),
    (re.compile(r"\bcurl\b.*\|\s*bash"), "piping remote script to shell"),
    (re.compile(r"\bwget\b.*\|\s*bash"), "piping remote script to shell"),
]


def _check_dangerous(command: str) -> str | None:
    """Return a warning string if *command* matches a dangerous pattern."""
    for pattern, reason in _DANGEROUS_PATTERNS:
        if pattern.search(command):
            return f"[warning] Dangerous command detected ({reason}). Command was still executed."
    return None


def _truncate_output(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    """Truncate *text* if it exceeds *limit* characters."""
    if len(text) <= limit:
        return text
    half = limit // 2
    omitted = len(text) - limit
    return (
        text[:half]
        + f"\n\n... [{omitted} characters truncated] ...\n\n"
        + text[-half:]
    )


class BashTool(BaseTool):
    """Run a bash command and return combined stdout + stderr.

    Tracks a persistent working directory across invocations so that
    ``cd`` in one call is honoured in subsequent calls.
    """

    name = "bash"
    description = (
        "Execute a bash command and return stdout/stderr. "
        "The working directory persists between calls."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 120).",
            },
        },
        "required": ["command"],
    }

    def __init__(self) -> None:
        super().__init__()
        self._cwd: str = os.getcwd()

    async def execute(self, **kwargs: Any) -> str:  # noqa: C901
        command: str = kwargs["command"]
        timeout: int = kwargs.get("timeout", _DEFAULT_TIMEOUT)

        # Security check (warn but still execute)
        warning = _check_dangerous(command)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
                env={**os.environ, "TERM": "dumb"},
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return f"[error] Command timed out after {timeout}s"

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            # Update working directory if the command contained cd
            # We parse the cwd from a subshell echo after the real command
            if "cd " in command or "cd\t" in command:
                cwd_proc = await asyncio.create_subprocess_shell(
                    f"cd {self._cwd} && {command} && pwd",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    cwd=self._cwd,
                )
                cwd_out, _ = await asyncio.wait_for(
                    cwd_proc.communicate(),
                    timeout=5,
                )
                if cwd_proc.returncode == 0:
                    new_cwd = cwd_out.decode().strip().splitlines()[-1]
                    if os.path.isdir(new_cwd):
                        self._cwd = new_cwd

            # Build output
            parts: list[str] = []
            if warning:
                parts.append(warning)
            if stdout.strip():
                parts.append(stdout.rstrip())
            if stderr.strip():
                parts.append(stderr.rstrip())
            output = "\n".join(parts)

            output = _truncate_output(output)

            if proc.returncode != 0:
                return f"[exit code {proc.returncode}]\n{output}" if output else f"[exit code {proc.returncode}]"

            return output or "(no output)"

        except Exception as exc:
            return f"[error] {exc}"
