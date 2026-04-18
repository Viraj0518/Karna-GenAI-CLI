"""System environment context injected into every conversation.

Provides platform, shell, Python version, working directory, and date
so the model can tailor advice (e.g. Windows vs Linux paths).
"""

from __future__ import annotations

import os
import platform
from datetime import date
from pathlib import Path


class EnvironmentContext:
    """Gather system environment metadata."""

    def get_context(self, cwd: Path | None = None) -> str:
        """Return a multi-line string with environment details."""
        parts: list[str] = [
            f"Platform: {self._platform_string()}",
            f"Shell: {self._shell()}",
            f"Python: {platform.python_version()}",
            f"Working directory: {cwd or Path.cwd()}",
            f"Date: {date.today().isoformat()}",
        ]
        return "\n".join(parts)

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _platform_string() -> str:
        """E.g. ``linux (Ubuntu 22.04)`` or ``darwin (macOS 14.3)``."""
        system = platform.system().lower()
        if system == "linux":
            try:
                import distro  # type: ignore[import-untyped]

                name = distro.name(pretty=True)
                return f"linux ({name})"
            except ImportError:
                return "linux"
        elif system == "darwin":
            ver = platform.mac_ver()[0]
            return f"darwin (macOS {ver})" if ver else "darwin"
        elif system == "windows":
            ver = platform.version()
            return f"windows ({ver})" if ver else "windows"
        return system

    @staticmethod
    def _shell() -> str:
        """Best-effort detection of the user's shell."""
        shell = os.environ.get("SHELL", "")
        if shell:
            return Path(shell).name
        # Windows fallback
        comspec = os.environ.get("COMSPEC", "")
        if comspec:
            return Path(comspec).name
        return "unknown"
