"""Tool registry for Karna.

Maps tool names to their implementation classes and exposes
``get_tool()`` and ``get_all_tools()`` helpers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from karna.tools.base import BaseTool

# Lazy imports to avoid circular deps and keep startup fast.
_TOOL_PATHS: dict[str, tuple[str, str]] = {
    "bash": ("karna.tools.bash", "BashTool"),
    "read": ("karna.tools.read", "ReadTool"),
    "write": ("karna.tools.write", "WriteTool"),
    "edit": ("karna.tools.edit", "EditTool"),
    "grep": ("karna.tools.grep", "GrepTool"),
    "glob": ("karna.tools.glob", "GlobTool"),
    "web_search": ("karna.tools.web_search", "WebSearchTool"),
    "web_fetch": ("karna.tools.web_fetch", "WebFetchTool"),
    "clipboard": ("karna.tools.clipboard", "ClipboardTool"),
    "image": ("karna.tools.image", "ImageTool"),
}
# MCP tools are registered dynamically at runtime via MCPClientTool

# Public alias — maps tool name → (module, class) for introspection.
TOOLS: dict[str, tuple[str, str]] = dict(_TOOL_PATHS)


def get_tool(name: str) -> "BaseTool":
    """Instantiate and return the tool registered under *name*.

    Raises ``KeyError`` if the tool is not registered.
    """
    key = name.lower()
    if key not in _TOOL_PATHS:
        raise KeyError(
            f"Unknown tool: {name!r}. "
            f"Available: {', '.join(sorted(_TOOL_PATHS))}"
        )
    import importlib

    module_path, class_name = _TOOL_PATHS[key]
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return cls()


def get_all_tools() -> list["BaseTool"]:
    """Instantiate and return one instance of every registered tool."""
    return [get_tool(name) for name in _TOOL_PATHS]
