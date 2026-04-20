"""Hermes-style tool call preview and emoji mapping.

Ported from hermes-agent ``agent/display.py``. Provides one-line tool call
previews and emoji resolution for the Nellie TUI. Uses Rich (not raw ANSI)
for rendering.

Public API:
    get_tool_emoji(tool_name)      -- emoji for a tool
    build_tool_preview(tool_name, args)  -- one-line preview string
    get_cute_tool_message(tool_name, args, duration, result)  -- completion line
"""

from __future__ import annotations

import json as _json

# --------------------------------------------------------------------------- #
#  Tool emoji mapping
# --------------------------------------------------------------------------- #

_TOOL_EMOJIS: dict[str, str] = {
    "bash": "\U0001f4bb",  # laptop
    "read": "\U0001f4d6",  # open book
    "write": "\u270d\ufe0f",  # writing hand
    "edit": "\U0001f527",  # wrench
    "grep": "\U0001f50e",  # magnifying glass right
    "glob": "\U0001f50d",  # magnifying glass left
    "web_search": "\U0001f50d",
    "web_fetch": "\U0001f4c4",  # page facing up
    "git": "\U0001f33f",  # herb (branch)
    "mcp": "\U0001f50c",  # plug
    "task": "\U0001f4cb",  # clipboard
    "monitor": "\U0001f4ca",  # chart
}


def get_tool_emoji(tool_name: str, default: str = "\u26a1") -> str:
    """Return the display emoji for a tool name.

    Falls back to *default* (lightning bolt) for unknown tools.
    """
    return _TOOL_EMOJIS.get(tool_name, default)


# --------------------------------------------------------------------------- #
#  One-line tool preview
# --------------------------------------------------------------------------- #


def _oneline(text: str) -> str:
    """Collapse whitespace (including newlines) to single spaces."""
    return " ".join(text.split())


def build_tool_preview(tool_name: str, args: dict, max_len: int = 60) -> str | None:
    """Build a short one-line preview of a tool call's primary argument.

    Returns ``None`` when no meaningful preview can be generated.
    """
    if not args:
        return None

    # Primary argument lookup table -- maps tool name to the argument key
    # that best summarises what the tool is doing.
    primary_args: dict[str, str] = {
        "bash": "command",
        "read": "file_path",
        "write": "file_path",
        "edit": "file_path",
        "grep": "pattern",
        "glob": "pattern",
        "web_search": "query",
        "web_fetch": "url",
        "git": "command",
        "mcp": "server",
        "task": "description",
        "monitor": "command",
    }

    key = primary_args.get(tool_name)
    if not key:
        # Fall back to common argument names
        for fallback_key in ("query", "text", "command", "path", "file_path", "name", "prompt", "code", "goal"):
            if fallback_key in args:
                key = fallback_key
                break

    if not key or key not in args:
        return None

    value = args[key]
    if isinstance(value, list):
        value = value[0] if value else ""

    preview = _oneline(str(value))
    if not preview:
        return None
    if max_len > 0 and len(preview) > max_len:
        preview = preview[: max_len - 3] + "..."
    return preview


# --------------------------------------------------------------------------- #
#  Cute tool-completion message (replaces spinner on completion)
# --------------------------------------------------------------------------- #


def _detect_tool_failure(tool_name: str, result: str | None) -> tuple[bool, str]:
    """Inspect a tool result for signs of failure.

    Returns ``(is_failure, suffix)`` -- suffix is like ``" [exit 1]"`` or
    ``" [error]"``.  On success returns ``(False, "")``.
    """
    if result is None:
        return False, ""

    if tool_name == "bash":
        try:
            data = _json.loads(result)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict):
            exit_code = data.get("exit_code")
            if exit_code is not None and exit_code != 0:
                return True, f" [exit {exit_code}]"
        return False, ""

    # Generic heuristic
    lower = result[:500].lower()
    if '"error"' in lower or '"failed"' in lower or result.startswith("Error"):
        return True, " [error]"
    return False, ""


def get_cute_tool_message(
    tool_name: str,
    args: dict,
    duration: float,
    result: str | None = None,
) -> str:
    """Generate a hermes-style tool completion line.

    Format: ``| {emoji} {verb:9} {detail}  {duration}``
    """
    dur = f"{duration:.1f}s"
    is_failure, failure_suffix = _detect_tool_failure(tool_name, result)
    emoji = get_tool_emoji(tool_name)

    def _trunc(s: str, n: int = 40) -> str:
        s = str(s)
        return (s[: n - 3] + "...") if len(s) > n else s

    def _path(p: str, n: int = 35) -> str:
        p = str(p)
        return ("..." + p[-(n - 3) :]) if len(p) > n else p

    def _wrap(line: str) -> str:
        if not is_failure:
            return line
        return f"{line}{failure_suffix}"

    if tool_name == "bash":
        return _wrap(f"\u250a {emoji} $         {_trunc(args.get('command', ''), 42)}  {dur}")
    if tool_name == "read":
        return _wrap(f"\u250a {emoji} read      {_path(args.get('file_path', ''))}  {dur}")
    if tool_name == "write":
        return _wrap(f"\u250a {emoji} write     {_path(args.get('file_path', ''))}  {dur}")
    if tool_name == "edit":
        return _wrap(f"\u250a {emoji} edit      {_path(args.get('file_path', ''))}  {dur}")
    if tool_name == "grep":
        return _wrap(f"\u250a {emoji} grep      {_trunc(args.get('pattern', ''), 35)}  {dur}")
    if tool_name == "glob":
        return _wrap(f"\u250a {emoji} glob      {_trunc(args.get('pattern', ''), 35)}  {dur}")
    if tool_name == "web_search":
        return _wrap(f"\u250a {emoji} search    {_trunc(args.get('query', ''), 42)}  {dur}")
    if tool_name == "web_fetch":
        url = args.get("url", "")
        domain = url.replace("https://", "").replace("http://", "").split("/")[0]
        return _wrap(f"\u250a {emoji} fetch     {_trunc(domain, 35)}  {dur}")

    preview = build_tool_preview(tool_name, args) or ""
    return _wrap(f"\u250a {emoji} {tool_name[:9]:9} {_trunc(preview, 35)}  {dur}")


__all__ = [
    "get_tool_emoji",
    "build_tool_preview",
    "get_cute_tool_message",
]
