"""Glob tool — file pattern matching.

Stub for Phase 2 — will use pathlib glob with sorting.
"""

from __future__ import annotations

from typing import Any

from karna.tools.base import BaseTool


class GlobTool(BaseTool):
    """Find files matching glob patterns (Phase 2)."""

    name = "glob"
    description = "Find files matching a glob pattern."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern (e.g. '**/*.py').",
            },
            "path": {
                "type": "string",
                "description": "Root directory to search from.",
            },
        },
        "required": ["pattern"],
    }

    async def execute(self, **kwargs: Any) -> str:
        raise NotImplementedError("Phase 2 — glob tool not yet implemented.")
