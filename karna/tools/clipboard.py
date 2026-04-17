"""Clipboard tool — read from and write to the system clipboard.

Cross-platform support via subprocess calls to native clipboard
utilities:

- macOS: ``pbcopy`` / ``pbpaste``
- Linux X11: ``xclip -selection clipboard``
- Linux Wayland: ``wl-copy`` / ``wl-paste``
- WSL: ``powershell.exe -command Get-Clipboard / Set-Clipboard``

Privacy: operates on the local system clipboard only.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
from typing import Any

from karna.tools.base import BaseTool


# ----------------------------------------------------------------------- #
#  Platform detection
# ----------------------------------------------------------------------- #


def _detect_platform() -> str:
    """Detect the clipboard platform.

    Returns one of: ``macos``, ``wayland``, ``x11``, ``wsl``, or ``unsupported``.
    """
    system = platform.system()

    if system == "Darwin":
        return "macos"

    if system == "Linux":
        # Check WSL first
        try:
            with open("/proc/version", "r") as f:
                version_info = f.read().lower()
            if "microsoft" in version_info or "wsl" in version_info:
                return "wsl"
        except OSError:
            pass

        # Wayland check
        if os.environ.get("WAYLAND_DISPLAY"):
            return "wayland"

        # X11 check
        if os.environ.get("DISPLAY"):
            return "x11"

        # Headless — try x11 utilities anyway (might be available)
        if shutil.which("xclip") or shutil.which("xsel"):
            return "x11"

        return "unsupported"

    if system == "Windows":
        # Unlikely to run directly on Windows, but handle it
        return "wsl"  # powershell commands work on native Windows too

    return "unsupported"


def _get_copy_cmd(plat: str) -> list[str] | None:
    """Return the command to write stdin to clipboard."""
    if plat == "macos":
        return ["pbcopy"]
    elif plat == "wayland":
        if shutil.which("wl-copy"):
            return ["wl-copy"]
    elif plat == "x11":
        if shutil.which("xclip"):
            return ["xclip", "-selection", "clipboard"]
        if shutil.which("xsel"):
            return ["xsel", "--clipboard", "--input"]
    elif plat == "wsl":
        clip_exe = shutil.which("clip.exe")
        if clip_exe:
            return [clip_exe]
        ps = shutil.which("powershell.exe")
        if ps:
            return [ps, "-NoProfile", "-Command", "Set-Clipboard -Value $input"]
    return None


def _get_paste_cmd(plat: str) -> list[str] | None:
    """Return the command to read clipboard to stdout."""
    if plat == "macos":
        return ["pbpaste"]
    elif plat == "wayland":
        if shutil.which("wl-paste"):
            return ["wl-paste", "--no-newline"]
    elif plat == "x11":
        if shutil.which("xclip"):
            return ["xclip", "-selection", "clipboard", "-o"]
        if shutil.which("xsel"):
            return ["xsel", "--clipboard", "--output"]
    elif plat == "wsl":
        ps = shutil.which("powershell.exe")
        if ps:
            return [ps, "-NoProfile", "-Command", "Get-Clipboard"]
    return None


# ----------------------------------------------------------------------- #
#  Clipboard Tool
# ----------------------------------------------------------------------- #


class ClipboardTool(BaseTool):
    """Read from or write to the system clipboard."""

    name = "clipboard"
    description = "Read from or write to the system clipboard."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "write"],
                "description": "Read from or write to clipboard",
            },
            "content": {
                "type": "string",
                "description": "Content to write (only for write action)",
            },
        },
        "required": ["action"],
    }

    async def execute(self, **kwargs: Any) -> str:
        """Read or write clipboard content."""
        action = kwargs.get("action", "")
        content = kwargs.get("content")

        if action not in ("read", "write"):
            return f"[error] Invalid action: {action!r}. Must be 'read' or 'write'."

        plat = _detect_platform()
        if plat == "unsupported":
            return (
                "[error] No clipboard utility found. "
                "Install xclip, xsel, wl-clipboard, or run on macOS/WSL."
            )

        if action == "read":
            return await self._read(plat)
        else:
            if content is None:
                return "[error] 'content' is required for write action."
            return await self._write(plat, content)

    async def _read(self, plat: str) -> str:
        """Read clipboard contents."""
        cmd = _get_paste_cmd(plat)
        if cmd is None:
            return "[error] No paste command available for this platform."

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        except FileNotFoundError:
            return "[error] Clipboard utility not found. Install xclip or xsel."
        except asyncio.TimeoutError:
            return "[error] Clipboard read timed out."

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            return f"[error] Clipboard read failed: {err or 'unknown error'}"

        text = stdout.decode(errors="replace")
        if not text:
            return "(clipboard is empty)"
        return text

    async def _write(self, plat: str, content: str) -> str:
        """Write content to clipboard."""
        cmd = _get_copy_cmd(plat)
        if cmd is None:
            return "[error] No copy command available for this platform."

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(
                proc.communicate(input=content.encode()),
                timeout=10,
            )
        except FileNotFoundError:
            return "[error] Clipboard utility not found. Install xclip or xsel."
        except asyncio.TimeoutError:
            return "[error] Clipboard write timed out."

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            return f"[error] Clipboard write failed: {err or 'unknown error'}"

        char_count = len(content)
        return f"Copied {char_count} characters to clipboard."
