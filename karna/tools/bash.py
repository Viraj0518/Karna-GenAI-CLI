"""Bash tool -- executes shell commands via asyncio subprocess.

Full implementation ported from cc-src BashTool with attribution to
the Anthropic Claude Code codebase.

Features:
- Async subprocess execution via ``asyncio.create_subprocess_shell``
- stdout + stderr capture
- Configurable timeout (default 120 s)
- Working directory tracking
- Output truncation for large results
- Dangerous-command detection via security guards
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from karna.security.guards import check_dangerous_command
from karna.tools.base import BaseTool

_DEFAULT_TIMEOUT = 120  # seconds
_MAX_OUTPUT_CHARS = 100_000  # truncate beyond this


def _truncate_output(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    """Truncate *text* if it exceeds *limit* characters."""
    if len(text) <= limit:
        return text
    half = limit // 2
    omitted = len(text) - limit
    return text[:half] + f"\n\n... [{omitted} characters truncated] ...\n\n" + text[-half:]


class BashTool(BaseTool):
    """Run a bash command and return combined stdout + stderr.

    Tracks a persistent working directory across invocations so that
    ``cd`` in one call is honoured in subsequent calls.

    Security:
    - Checks ``check_dangerous_command()`` before execution.
    - If dangerous and ``safe_mode`` is enabled, BLOCKS execution.
    - If dangerous and ``safe_mode`` is disabled, returns a warning
      but still executes.
    """

    name = "bash"
    sequential = True  # Shell commands must not run concurrently
    description = "Execute a bash command and return stdout/stderr. The working directory persists between calls."
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

    def __init__(self, *, safe_mode: bool = False) -> None:
        super().__init__()
        self._cwd: str = os.getcwd()
        self._safe_mode = safe_mode

    async def execute(self, **kwargs: Any) -> str:  # noqa: C901
        command: str = kwargs["command"]
        timeout: int = kwargs.get("timeout", _DEFAULT_TIMEOUT)

        # Security check
        warning = check_dangerous_command(command)
        if warning:
            if self._safe_mode:
                return (
                    f"[BLOCKED] {warning}. "
                    "Command was NOT executed (safe-mode is enabled). "
                    "Disable safe-mode in config to allow dangerous commands."
                )

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
                parts.append(f"[warning] {warning}. Command was still executed.")
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
