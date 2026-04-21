"""Read tool -- reads file contents from disk with line numbers.

Full implementation ported from cc-src FileReadTool with attribution to
the Anthropic Claude Code codebase.

Features:
- Line-numbered output (``cat -n`` style)
- ``offset`` + ``limit`` for partial reads
- Binary file detection
- Image file detection (placeholder)
- Default max 2000 lines
- Path safety checks via ``is_safe_path()``
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from karna.prompts.cc_tool_prompts import CC_TOOL_PROMPTS
from karna.security.guards import is_safe_path
from karna.tools.base import BaseTool

_DEFAULT_MAX_LINES = 2000

# Common image extensions
_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "ico", "tiff", "tif"})

# Binary extensions that should not be displayed as text
_BINARY_EXTENSIONS = frozenset(
    {
        "exe",
        "dll",
        "so",
        "dylib",
        "bin",
        "o",
        "a",
        "lib",
        "pyc",
        "pyo",
        "class",
        "jar",
        "war",
        "zip",
        "gz",
        "bz2",
        "xz",
        "tar",
        "7z",
        "rar",
        "pdf",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "ppt",
        "pptx",
        "wasm",
        "dat",
        "db",
        "sqlite",
        "sqlite3",
        "mp3",
        "mp4",
        "avi",
        "mov",
        "mkv",
        "wav",
        "flac",
        "ttf",
        "otf",
        "woff",
        "woff2",
        "eot",
    }
)


def _is_binary(path: Path) -> bool:
    """Heuristic binary detection: extension check + null-byte sniff."""
    ext = path.suffix.lstrip(".").lower()
    if ext in _BINARY_EXTENSIONS:
        return True
    # Sniff first 8 KB for null bytes
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(8192)
            if b"\x00" in chunk:
                return True
    except OSError:
        pass
    return False


def _is_image(path: Path) -> bool:
    ext = path.suffix.lstrip(".").lower()
    return ext in _IMAGE_EXTENSIONS


class ReadTool(BaseTool):
    """Read a file from the local filesystem and return its contents
    with line numbers (``cat -n`` style).

    Security: rejects reads of credential files, ~/.ssh, and other
    sensitive paths via ``is_safe_path()``.
    """

    name = "read"
    description = (
        "Read a file and return its contents with line numbers. "
        "Supports offset + limit for partial reads. "
        "Binary files are detected and skipped."
    )
    cc_prompt = CC_TOOL_PROMPTS["read"]
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to read.",
            },
            "offset": {
                "type": "integer",
                "description": ("Line number to start reading from (1-based). Defaults to 1."),
            },
            "limit": {
                "type": "integer",
                "description": ("Maximum number of lines to return. Defaults to 2000."),
            },
        },
        "required": ["file_path"],
    }

    def __init__(self, *, allowed_roots: list[Path] | None = None) -> None:
        super().__init__()
        self._allowed_roots = allowed_roots

    async def execute(self, **kwargs: Any) -> str:
        file_path_str: str = kwargs["file_path"]
        offset: int = kwargs.get("offset", 1)
        limit: int = kwargs.get("limit", _DEFAULT_MAX_LINES)

        # Security: path safety check
        if not is_safe_path(file_path_str, allowed_roots=self._allowed_roots):
            return (
                f"[error] Access denied: {file_path_str} is outside the "
                "allowed directory or points to a sensitive location."
            )

        # Expand ~ and resolve
        file_path = Path(os.path.expanduser(file_path_str)).resolve()

        if not file_path.exists():
            return f"[error] File not found: {file_path}"
        if not file_path.is_file():
            return f"[error] Not a file: {file_path}"

        # Image detection
        if _is_image(file_path):
            size = file_path.stat().st_size
            return f"[image file: {file_path.suffix.lstrip('.')} format, {size} bytes -- visual content not displayed]"

        # Binary detection
        if _is_binary(file_path):
            return f"[binary file: {file_path.suffix.lstrip('.')} format -- cannot display]"

        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return f"[error] {exc}"

        all_lines = text.splitlines()
        total_lines = len(all_lines)

        # offset is 1-based; convert to 0-based index
        start_idx = max(0, offset - 1)
        selected = all_lines[start_idx : start_idx + limit]

        if not selected and total_lines == 0:
            return "(empty file)"

        if not selected:
            return f"[warning] Offset {offset} is past end of file ({total_lines} lines total)."

        numbered = [f"{i + start_idx + 1}\t{line}" for i, line in enumerate(selected)]
        return "\n".join(numbered)
