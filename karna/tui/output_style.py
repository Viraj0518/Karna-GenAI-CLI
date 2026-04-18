"""Output presentation styles for Nellie (CC's ``/output-style`` analogue).

Five built-in styles are provided:

* ``default``    — the current Rich look, brand-accented panels.
* ``minimal``    — plain text, no panels, no borders.
* ``verbose``    — full tool args expanded, timestamps, cost per turn.
* ``compact``    — single-line tool calls, no blank-lines between turns.
* ``dark-code``  — code blocks stay bright; everything else dimmed.

Each style is a thin object implementing the :class:`OutputStyle` protocol.
The active style name is read from ``~/.karna/config.toml`` (``[tui].output_style``)
via :func:`active_style_name`; resolution is done through :func:`get_style`
which always returns a valid style (falls back to ``default``).

No rewrite of ``karna.tui.output`` is attempted here — that renderer is
owned by another agent. These classes are drop-in renderables that any
consumer can pick up when it is ready.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from rich.console import RenderableType
from rich.panel import Panel
from rich.text import Text

try:
    import tomllib  # Py 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

try:
    from karna.tui.design_tokens import SEMANTIC

    _ASSIST = SEMANTIC.get("role.assistant", "#87CEEB")
    _TOOL = SEMANTIC.get("tool.name", "#3C73BD")
    _META = SEMANTIC.get("meta", "#A0A4AD")
    _DIM = SEMANTIC.get("text.tertiary", "#5F6472")
    _BORDER = SEMANTIC.get("border.subtle", "#2A2F38")
except Exception:  # pragma: no cover
    _ASSIST, _TOOL, _META, _DIM, _BORDER = "cyan", "blue", "grey70", "grey50", "grey35"


_CONFIG_PATH = Path.home() / ".karna" / "config.toml"


# --------------------------------------------------------------------------- #
#  Protocol
# --------------------------------------------------------------------------- #


@runtime_checkable
class OutputStyle(Protocol):
    name: str

    def format_assistant(self, content: str) -> RenderableType: ...

    def format_tool_header(self, tool: str, args: dict) -> RenderableType: ...


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #


def _short_args(args: dict) -> str:
    """Collapse a tool-args dict into a one-line summary."""
    try:
        blob = json.dumps(args, separators=(",", ":"), default=str)
    except Exception:
        blob = str(args)
    return blob if len(blob) <= 80 else blob[:77] + "..."


# --------------------------------------------------------------------------- #
#  Built-ins
# --------------------------------------------------------------------------- #


class _DefaultStyle:
    name = "default"

    def format_assistant(self, content: str) -> RenderableType:
        return Text(content, style=_ASSIST)

    def format_tool_header(self, tool: str, args: dict) -> RenderableType:
        t = Text()
        t.append(f"> {tool}", style=f"bold {_TOOL}")
        t.append(f"  {_short_args(args)}", style=_META)
        return t


class _MinimalStyle:
    name = "minimal"

    def format_assistant(self, content: str) -> RenderableType:
        return Text(content)

    def format_tool_header(self, tool: str, args: dict) -> RenderableType:
        return Text(f"{tool}: {_short_args(args)}")


class _VerboseStyle:
    name = "verbose"

    def format_assistant(self, content: str) -> RenderableType:
        ts = datetime.now().isoformat(timespec="seconds")
        body = Text()
        body.append(f"[{ts}] ", style=_DIM)
        body.append(content, style=_ASSIST)
        return Panel(body, border_style=_BORDER, title="assistant", title_align="left")

    def format_tool_header(self, tool: str, args: dict) -> RenderableType:
        ts = datetime.now().isoformat(timespec="seconds")
        try:
            pretty = json.dumps(args, indent=2, default=str)
        except Exception:
            pretty = str(args)
        body = Text()
        body.append(f"[{ts}] ", style=_DIM)
        body.append(tool, style=f"bold {_TOOL}")
        body.append("\n")
        body.append(pretty, style=_META)
        return Panel(body, border_style=_BORDER, title="tool call", title_align="left")


class _CompactStyle:
    name = "compact"

    def format_assistant(self, content: str) -> RenderableType:
        # Collapse internal newlines; the whole turn sits on its own line.
        flat = " ".join(content.split())
        return Text(flat, style=_ASSIST, no_wrap=False)

    def format_tool_header(self, tool: str, args: dict) -> RenderableType:
        return Text(f"{tool}({_short_args(args)})", style=f"{_TOOL}")


class _DarkCodeStyle:
    name = "dark-code"

    def format_assistant(self, content: str) -> RenderableType:
        # Prose dimmed; fenced code blocks left at full contrast.
        out = Text()
        in_code = False
        for line in content.splitlines(keepends=True):
            stripped = line.lstrip()
            if stripped.startswith("```"):
                in_code = not in_code
                out.append(line, style=_META)
                continue
            out.append(line, style=None if in_code else f"dim {_DIM}")
        return out

    def format_tool_header(self, tool: str, args: dict) -> RenderableType:
        t = Text()
        t.append(tool, style=f"bold {_TOOL}")
        t.append(f" {_short_args(args)}", style=f"dim {_DIM}")
        return t


BUILTIN_STYLES: Mapping[str, OutputStyle] = {
    "default": _DefaultStyle(),
    "minimal": _MinimalStyle(),
    "verbose": _VerboseStyle(),
    "compact": _CompactStyle(),
    "dark-code": _DarkCodeStyle(),
}


# --------------------------------------------------------------------------- #
#  Resolution
# --------------------------------------------------------------------------- #


def _read_toml(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return {}


def active_style_name(config_path: Path | None = None) -> str:
    """Return the configured style name or ``"default"``."""
    data = _read_toml(config_path or _CONFIG_PATH)
    tui = data.get("tui") or {}
    name = tui.get("output_style")
    return name if isinstance(name, str) and name in BUILTIN_STYLES else "default"


def get_style(name: str) -> OutputStyle:
    """Look up a style by name; unknown names return ``default``."""
    return BUILTIN_STYLES.get(name, BUILTIN_STYLES["default"])


__all__ = [
    "OutputStyle",
    "BUILTIN_STYLES",
    "get_style",
    "active_style_name",
]
