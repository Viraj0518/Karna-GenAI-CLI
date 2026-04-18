"""Edit tool -- performs exact string replacements in files.

Full implementation ported from cc-src FileEditTool with attribution to
the Anthropic Claude Code codebase.

Features:
- ``old_string`` / ``new_string`` exact replacement
- ``replace_all`` flag for multiple occurrences
- Uniqueness check (old_string must be unique unless replace_all)
- New-file creation when old_string is empty and file doesn't exist
- Preserves file encoding
- Path safety checks via ``is_safe_path()``
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from karna.security.guards import is_safe_path
from karna.tools.base import BaseTool


class EditTool(BaseTool):
    """Perform exact string replacements in a file.

    The tool finds ``old_string`` in the file and replaces it with
    ``new_string``.  If ``replace_all`` is *True*, every occurrence is
    replaced; otherwise the string must be unique.

    Security: rejects edits to credential files, ~/.ssh, and other
    sensitive paths via ``is_safe_path()``.
    """

    name = "edit"
    sequential = True  # File edits must not run concurrently
    description = (
        "Replace an exact string in a file with new content. old_string must be unique unless replace_all is true."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to edit.",
            },
            "old_string": {
                "type": "string",
                "description": "The exact string to find and replace.",
            },
            "new_string": {
                "type": "string",
                "description": "The replacement string.",
            },
            "replace_all": {
                "type": "boolean",
                "description": ("Replace all occurrences of old_string (default false)."),
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    def __init__(self, *, allowed_roots: list[Path] | None = None) -> None:
        super().__init__()
        self._allowed_roots = allowed_roots

    async def execute(self, **kwargs: Any) -> str:
        file_path_str: str = kwargs["file_path"]
        old_string: str = kwargs["old_string"]
        new_string: str = kwargs["new_string"]
        replace_all: bool = kwargs.get("replace_all", False)

        # Security: path safety check
        if not is_safe_path(file_path_str, allowed_roots=self._allowed_roots):
            return (
                f"[error] Access denied: {file_path_str} is outside the "
                "allowed directory or points to a sensitive location."
            )

        file_path = Path(os.path.expanduser(file_path_str)).resolve()

        # ---- No-op guard ------------------------------------------------
        if old_string == new_string:
            return "[error] No changes to make: old_string and new_string are exactly the same."

        # ---- New file creation (empty old_string, file doesn't exist) ----
        if old_string == "" and not file_path.exists():
            try:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(new_string, encoding="utf-8")
                return f"File created successfully at: {file_path}"
            except Exception as exc:
                return f"[error] {exc}"

        # ---- File must exist for replacement -----------------------------
        if not file_path.exists():
            return f"[error] File does not exist: {file_path}"

        if not file_path.is_file():
            return f"[error] Not a file: {file_path}"

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return f"[error] {exc}"

        # ---- Empty old_string on existing file with content ---------------
        if old_string == "":
            if content.strip():
                return "[error] Cannot create new file -- file already exists and has content."
            # Empty file -- treat as full replacement
            try:
                file_path.write_text(new_string, encoding="utf-8")
                return f"The file {file_path} has been updated successfully."
            except Exception as exc:
                return f"[error] {exc}"

        # ---- old_string not found ----------------------------------------
        if old_string not in content:
            return f"[error] String to replace not found in file.\nString: {old_string}"

        # ---- Uniqueness check (unless replace_all) -----------------------
        match_count = content.count(old_string)
        if match_count > 1 and not replace_all:
            return (
                f"[error] Found {match_count} matches of the string to "
                f"replace, but replace_all is false. To replace all "
                f"occurrences, set replace_all to true. To replace only "
                f"one occurrence, please provide more context to uniquely "
                f"identify the instance.\nString: {old_string}"
            )

        # ---- Perform replacement -----------------------------------------
        if replace_all:
            updated = content.replace(old_string, new_string)
        else:
            updated = content.replace(old_string, new_string, 1)

        try:
            file_path.write_text(updated, encoding="utf-8")
        except Exception as exc:
            return f"[error] {exc}"

        if replace_all and match_count > 1:
            return f"The file {file_path} has been updated successfully. All {match_count} occurrences were replaced."

        return f"The file {file_path} has been updated successfully."
