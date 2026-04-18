"""Auto-generate tool documentation sections from the tool registry.

Each tool's name, description, and usage guidance are formatted into
a section suitable for inclusion in the system prompt.  The output is
model-agnostic — template-level formatting (XML vs markdown) is handled
by the caller.

Ported from cc-src tool prompt patterns with attribution to the
Anthropic Claude Code codebase.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from karna.tools.base import BaseTool

# ------------------------------------------------------------------ #
#  Per-tool usage guidance
# ------------------------------------------------------------------ #

# Maps tool name -> (when_to_use, when_not_to_use).
# Tools not listed here get a generic entry built from their description.
_TOOL_GUIDANCE: dict[str, tuple[str, str]] = {
    "bash": (
        "running tests, git operations, installing packages, checking system state, "
        "terminal operations that require shell execution",
        "reading files (use read), searching files (use grep/glob), "
        "editing files (use edit), writing files (use write)",
    ),
    "read": (
        "examining code, checking configs, understanding existing files before editing. "
        "Prefer this over `cat` in bash. Supports offset+limit for large files",
        "searching across many files (use grep/glob instead)",
    ),
    "write": (
        "creating new files, completely rewriting file contents. Creates parent directories automatically",
        "modifying existing files (use edit instead), appending to files (use edit instead)",
    ),
    "edit": (
        "modifying existing files via exact string replacement. "
        "Must read the file first. Supports replace_all for renaming",
        "creating new files from scratch (use write), replacing content you haven't verified exists in the file",
    ),
    "grep": (
        "searching file contents with regex patterns, finding usages, "
        "locating definitions. Supports glob filters and context lines",
        "finding files by name (use glob instead)",
    ),
    "glob": (
        "finding files by name pattern (e.g., '**/*.py', 'src/**/*.ts'). Results sorted by modification time",
        "searching file contents (use grep instead)",
    ),
}


def _format_tool_section(tool: "BaseTool") -> str:
    """Format a single tool into a documentation block."""
    lines = [f"### {tool.name}"]
    lines.append(tool.description)

    guidance = _TOOL_GUIDANCE.get(tool.name)
    if guidance:
        use_for, not_for = guidance
        lines.append(f"Use for: {use_for}.")
        lines.append(f"Do NOT use for: {not_for}.")
    else:
        # Generic guidance — just include the description
        lines.append("Use when this tool's capabilities match the task at hand.")

    # Add parameter summary from JSON schema
    props = tool.parameters.get("properties", {})
    required = set(tool.parameters.get("required", []))
    if props:
        param_parts = []
        for pname, pschema in props.items():
            req_marker = " (required)" if pname in required else ""
            pdesc = pschema.get("description", "")
            param_parts.append(f"  - `{pname}`{req_marker}: {pdesc}")
        lines.append("Parameters:")
        lines.extend(param_parts)

    return "\n".join(lines)


def generate_tool_docs(tools: list["BaseTool"]) -> str:
    """Generate the tool usage section of the system prompt.

    Returns a markdown-formatted string documenting all available tools,
    their descriptions, usage guidance, and parameters.
    """
    if not tools:
        return "## Available Tools\n\nNo tools are currently available."

    sections = ["## Available Tools\n"]
    for tool in tools:
        sections.append(_format_tool_section(tool))

    return "\n\n".join(sections)
