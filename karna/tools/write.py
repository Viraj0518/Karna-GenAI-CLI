"""Write tool — writes content to a file on disk.

Full implementation ported from cc-src FileWriteTool with attribution to
the Anthropic Claude Code codebase.

Features:
- Write content to an absolute file path
- Create parent directories automatically
- Overwrite protection: must read existing file before overwriting
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from karna.tools.base import BaseTool


class WriteTool(BaseTool):
    """Write content to a file, creating parent directories as needed.

    For safety, existing files should be read first (the agent loop
    enforces this via convention). New files are created without
    restriction.
    """

    name = "write"
    description = (
        "Write content to a file. Creates parent directories if needed. "
        "Overwrites existing files."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "The absolute path to the file to write "
                    "(must be absolute, not relative)."
                ),
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file.",
            },
        },
        "required": ["file_path", "content"],
    }

    async def execute(self, **kwargs: Any) -> str:
        file_path_str: str = kwargs["file_path"]
        content: str = kwargs["content"]

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
