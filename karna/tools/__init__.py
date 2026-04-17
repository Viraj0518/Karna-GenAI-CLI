"""Tool registry for Karna.

Maps tool names to their implementation classes and exposes a
``get_tool()`` lookup helper.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from karna.tools.base import BaseTool

# Lazy imports to avoid circular deps and keep startup fast.
_TOOL_PATHS: dict[str, tuple[str, str]] = {
    "bash": ("karna.tools.bash", "BashTool"),
    "read": ("karna.tools.read", "ReadTool"),
    "edit": ("karna.tools.edit", "EditTool"),
    "grep": ("karna.tools.grep", "GrepTool"),
    "glob": ("karna.tools.glob", "GlobTool"),
}


def get_tool(name: str) -> "BaseTool":
    """Instantiate and return the tool registered under *name*.

    Raises ``KeyError`` if the tool is not registered.
    """
    key = name.lower()
    if key not in _TOOL_PATHS:
        raise KeyError(f"Unknown tool: {name!r}. Available: {', '.join(_TOOL_PATHS)}")
    import importlib

    module_path, class_name = _TOOL_PATHS[key]
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return cls()


TOOLS: dict[str, tuple[str, str]] = dict(_TOOL_PATHS)
