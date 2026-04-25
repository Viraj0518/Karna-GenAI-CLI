"""Write tool -- writes content to a file on disk.


Features:
- Write content to an absolute file path
- Create parent directories automatically
- Overwrite protection: must read existing file before overwriting
- Path safety checks via ``is_safe_path()``
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from karna.prompts.cc_tool_prompts import CC_TOOL_PROMPTS
from karna.security.guards import is_safe_path
from karna.tools.base import BaseTool


class WriteTool(BaseTool):
    """Write content to a file, creating parent directories as needed.

    For safety, existing files should be read first (the agent loop
    enforces this via convention). New files are created without
    restriction.

    Security: rejects writes to credential files, ~/.ssh, and other
    sensitive paths via ``is_safe_path()``. Writes outside cwd require
    explicit ``allowed_roots`` configuration.
    """

    name = "write"
    sequential = True  # File writes must not run concurrently
    description = "Write content to a file. Creates parent directories if needed. Overwrites existing files."
    cc_prompt = CC_TOOL_PROMPTS["write"]
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": ("The absolute path to the file to write (must be absolute, not relative)."),
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file.",
            },
        },
        "required": ["file_path", "content"],
    }

    def __init__(self, *, allowed_roots: list[Path] | None = None) -> None:
        super().__init__()
        self._allowed_roots = allowed_roots

    async def execute(self, **kwargs: Any) -> str:
        file_path_str: str = kwargs["file_path"]
        content: str = kwargs["content"]

        # Security: path safety check
        if not is_safe_path(file_path_str, allowed_roots=self._allowed_roots):
            return (
                f"[error] Access denied: {file_path_str} is outside the "
                "allowed directory or points to a sensitive location."
            )

        file_path = Path(os.path.expanduser(file_path_str)).resolve()

        try:
            is_new = not file_path.exists()

            # Ensure parent directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)

            file_path.write_text(content, encoding="utf-8")

            if is_new:
                return f"File created successfully at: {file_path}"
            else:
                return f"The file {file_path} has been updated successfully."

        except Exception as exc:
            return f"[error] {exc}"
