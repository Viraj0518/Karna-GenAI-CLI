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
- Background execution via ``run_in_background`` parameter

Security default (CRITICAL-1 fix):
``safe_mode`` now defaults to ``True``. When ``check_dangerous_command``
flags a command, BashTool BLOCKS execution and returns an explanatory
message. Callers must explicitly opt in by constructing
``BashTool(safe_mode=False)`` to allow dangerous commands to run with
only a warning. This is a deliberate behaviour flip from earlier
versions which defaulted to execute-with-warning.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Any
from uuid import uuid4

from karna.security.guards import check_dangerous_command
from karna.tools.base import BaseTool
from karna.tools.task_registry import TaskType, get_task_registry

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120  # seconds
_MAX_TIMEOUT = 600  # seconds
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

    When ``run_in_background=True``, the command is spawned as a
    background task. The tool returns immediately with a task ID and
    output file path. When the process completes, a notification is
    injected into the conversation via the task registry.

    Security:
    - Checks ``check_dangerous_command()`` before execution.
    - If dangerous and ``safe_mode`` is enabled, BLOCKS execution.
    - If dangerous and ``safe_mode`` is disabled, returns a warning
      but still executes.
    """

    name = "bash"
    sequential = True  # Shell commands must not run concurrently
    description = (
        "Execute a bash command and return stdout/stderr. "
        "The working directory persists between calls. "
        "Set run_in_background=true to spawn the process and return immediately."
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
                "description": "Timeout in seconds (default 120, max 600).",
            },
            "description": {
                "type": "string",
                "description": "Description of the command (for background task display).",
            },
            "run_in_background": {
                "type": "boolean",
                "default": False,
                "description": (
                    "When true, spawn the process in the background and return "
                    "immediately with a task ID. The result will be delivered "
                    "as a notification when the process completes."
                ),
            },
        },
        "required": ["command"],
    }

    def __init__(self, *, safe_mode: bool = True) -> None:
        super().__init__()
        self._cwd: str = os.getcwd()
        self._safe_mode = safe_mode
        self._background_tasks: dict[str, asyncio.Task[None]] = {}

    async def execute(self, **kwargs: Any) -> str:  # noqa: C901
        command: str = kwargs["command"]
        timeout: int = min(kwargs.get("timeout", _DEFAULT_TIMEOUT), _MAX_TIMEOUT)
        run_in_background: bool = kwargs.get("run_in_background", False)
        description: str = kwargs.get("description", command[:80])

        # Security check
        warning = check_dangerous_command(command)
        if warning:
            if self._safe_mode:
                return (
                    f"[BLOCKED by safe_mode] {warning}\n\n"
                    "To override, set safe_mode=False explicitly "
                    "in config or tool kwargs."
                )
            # else: warn and continue (explicit opt-in)

        if run_in_background:
            return await self._execute_background(command, timeout, description, warning)

        return await self._execute_sync(command, timeout, warning)

    async def _execute_sync(
        self,
        command: str,
        timeout: int,
        warning: str | None,
    ) -> str:
        """Run command synchronously and return output."""
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
                # Do NOT await proc.communicate()/wait() here — on Python 3.12
                # the pipe drain can deadlock with the open PIPE transports.
                # SIGKILL guarantees the process is dead; the OS reclaims
                # the pipe buffers once the transport GCs.
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

    async def _execute_background(
        self,
        command: str,
        timeout: int,
        description: str,
        warning: str | None,
    ) -> str:
        """Spawn command in background and return immediately with task ID."""
        task_id = f"bg_{uuid4().hex[:8]}"

        # Create a temp file for output capture
        output_file = os.path.join(tempfile.gettempdir(), f"karna_bg_{task_id}.log")

        # Register with the task registry
        registry = get_task_registry()

        async_task = asyncio.create_task(
            self._run_background_process(
                command,
                task_id,
                timeout,
                description,
                output_file,
                warning,
            ),
            name=f"bg-bash-{task_id}",
        )
        self._background_tasks[task_id] = async_task

        registry.register(
            task_id=task_id,
            task_type=TaskType.BASH,
            description=description,
            asyncio_task=async_task,
        )

        logger.info(
            "Background bash %s started: %s (timeout=%ds)",
            task_id,
            description,
            timeout,
        )

        return (
            f"Background task {task_id} started.\n"
            f"Command: {command}\n"
            f"Timeout: {timeout}s\n"
            f"Output file: {output_file}\n"
            f"You will be notified when the task completes."
        )

    async def _run_background_process(
        self,
        command: str,
        task_id: str,
        timeout: int,
        description: str,
        output_file: str,
        warning: str | None,
    ) -> None:
        """Run the background process and push completion notification."""
        registry = get_task_registry()
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
                # Do NOT await proc.communicate()/wait() here — on Python 3.12
                # the pipe drain can deadlock with the open PIPE transports.
                # SIGKILL guarantees the process is dead; the OS reclaims
                # the pipe buffers once the transport GCs.
                error_msg = f"Command timed out after {timeout}s"
                # Write to output file
                with open(output_file, "w") as f:
                    f.write(f"[error] {error_msg}\n")
                registry.fail_task(task_id, error_msg)
                return

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

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
                result_text = f"[exit code {proc.returncode}]\n{output}" if output else f"[exit code {proc.returncode}]"
            else:
                result_text = output or "(no output)"

            # Write result to output file
            with open(output_file, "w") as f:
                f.write(result_text)

            # Notify via registry
            registry.complete_task(
                task_id,
                f"Background command completed (exit code {proc.returncode}). "
                f"Output ({len(result_text)} chars) written to {output_file}. "
                f"Result preview: {result_text[:500]}",
            )

        except asyncio.CancelledError:
            with open(output_file, "w") as f:
                f.write("[cancelled]\n")
            raise
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            with open(output_file, "w") as f:
                f.write(f"[error] {error_msg}\n")
            registry.fail_task(task_id, error_msg)
