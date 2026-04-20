"""Hermes-style inline diff rendering for file-edit tool results.

Ported from hermes-agent ``agent/display.py``. Generates Rich-rendered
inline diffs for write/edit operations, showing the before/after changes
directly in the tool output stream. Recolored to Karna blue palette.

Public API:
    capture_edit_snapshot(tool_name, args)    -- snapshot before-state
    render_inline_diff(tool_name, result, snapshot)  -- Rich renderable diff
"""

from __future__ import annotations

import difflib
import json as _json
import os
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Group, RenderableType
from rich.text import Text

try:
    from karna.tui.design_tokens import SEMANTIC

    _ADDED_COLOR = SEMANTIC.get("accent.success", "#7DCFA1")
    _REMOVED_COLOR = SEMANTIC.get("accent.danger", "#E87C7C")
    _META_COLOR = SEMANTIC.get("text.tertiary", "#5F6472")
    _HEADER_COLOR = SEMANTIC.get("accent.cyan", "#87CEEB")
    _HUNK_COLOR = SEMANTIC.get("text.disabled", "#3A3D45")
except Exception:  # pragma: no cover - fallback
    _ADDED_COLOR, _REMOVED_COLOR = "green", "red"
    _META_COLOR, _HEADER_COLOR, _HUNK_COLOR = "grey50", "cyan", "grey30"

_MAX_INLINE_DIFF_FILES = 6
_MAX_INLINE_DIFF_LINES = 80


# --------------------------------------------------------------------------- #
#  Snapshot for before/after comparison
# --------------------------------------------------------------------------- #


@dataclass
class EditSnapshot:
    """Pre-tool filesystem snapshot for rendering diffs after edits."""

    paths: list[Path] = field(default_factory=list)
    before: dict[str, str | None] = field(default_factory=dict)


def _resolved_path(path: str) -> Path:
    """Resolve a possibly-relative filesystem path against cwd."""
    candidate = Path(os.path.expanduser(path))
    if candidate.is_absolute():
        return candidate
    return Path.cwd() / candidate


def _snapshot_text(path: Path) -> str | None:
    """Return UTF-8 file content, or None for missing/unreadable files."""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, UnicodeDecodeError, OSError):
        return None


def _display_diff_path(path: Path) -> str:
    """Prefer cwd-relative paths in diffs when available."""
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except Exception:
        return str(path)


def capture_edit_snapshot(tool_name: str, args: dict | None) -> EditSnapshot | None:
    """Capture before-state for tools that modify files.

    Returns ``None`` if the tool doesn't modify files or the path can't
    be resolved.
    """
    if not isinstance(args, dict):
        return None

    paths: list[Path] = []
    if tool_name in ("write", "edit"):
        p = args.get("file_path")
        if p:
            paths.append(_resolved_path(p))

    if not paths:
        return None

    snapshot = EditSnapshot(paths=paths)
    for path in paths:
        snapshot.before[str(path)] = _snapshot_text(path)
    return snapshot


# --------------------------------------------------------------------------- #
#  Diff generation
# --------------------------------------------------------------------------- #


def _diff_from_snapshot(snapshot: EditSnapshot | None) -> str | None:
    """Generate unified diff text from stored before-state and current files."""
    if not snapshot:
        return None

    chunks: list[str] = []
    for path in snapshot.paths:
        before = snapshot.before.get(str(path))
        after = _snapshot_text(path)
        if before == after:
            continue

        display_path = _display_diff_path(path)
        diff = "".join(
            difflib.unified_diff(
                [] if before is None else before.splitlines(keepends=True),
                [] if after is None else after.splitlines(keepends=True),
                fromfile=f"a/{display_path}",
                tofile=f"b/{display_path}",
            )
        )
        if diff:
            chunks.append(diff)

    if not chunks:
        return None
    return "".join(chunk if chunk.endswith("\n") else chunk + "\n" for chunk in chunks)


def _result_succeeded(result: str | None) -> bool:
    """Conservatively detect whether a tool result represents success."""
    if not result:
        return False
    try:
        data = _json.loads(result)
    except (ValueError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("error"):
        return False
    if "success" in data:
        return bool(data.get("success"))
    return True


# --------------------------------------------------------------------------- #
#  Rich rendering
# --------------------------------------------------------------------------- #


def _render_unified_diff_rich(diff: str) -> list[RenderableType]:
    """Render unified diff lines as Rich Text objects."""
    rendered: list[RenderableType] = []
    from_file: str | None = None

    for raw_line in diff.splitlines():
        line = Text()
        if raw_line.startswith("--- "):
            from_file = raw_line[4:].strip()
            continue
        if raw_line.startswith("+++ "):
            to_file = raw_line[4:].strip()
            if from_file or to_file:
                file_line = Text()
                file_line.append(f"{from_file or 'a/?'} \u2192 {to_file or 'b/?'}", style=_HEADER_COLOR)
                rendered.append(file_line)
            continue
        if raw_line.startswith("@@"):
            line.append(raw_line, style=_HUNK_COLOR)
            rendered.append(line)
            continue
        if raw_line.startswith("-"):
            line.append(raw_line, style=f"on #3D1414 {_REMOVED_COLOR}")
            rendered.append(line)
            continue
        if raw_line.startswith("+"):
            line.append(raw_line, style=f"on #143D14 {_ADDED_COLOR}")
            rendered.append(line)
            continue
        if raw_line.startswith(" "):
            line.append(raw_line, style=f"dim {_META_COLOR}")
            rendered.append(line)
            continue
        if raw_line:
            line.append(raw_line)
            rendered.append(line)

    return rendered


def _split_diff_sections(diff: str) -> list[str]:
    """Split a unified diff into per-file sections."""
    sections: list[list[str]] = []
    current: list[str] = []

    for line in diff.splitlines():
        if line.startswith("--- ") and current:
            sections.append(current)
            current = [line]
            continue
        current.append(line)

    if current:
        sections.append(current)

    return ["\n".join(section) for section in sections if section]


def _summarize_diff_sections(
    diff: str,
    *,
    max_files: int = _MAX_INLINE_DIFF_FILES,
    max_lines: int = _MAX_INLINE_DIFF_LINES,
) -> list[RenderableType]:
    """Render diff sections while capping file count and total line count."""
    sections = _split_diff_sections(diff)
    rendered: list[RenderableType] = []
    omitted_files = 0
    omitted_lines = 0

    for idx, section in enumerate(sections):
        if idx >= max_files:
            omitted_files += 1
            omitted_lines += len(_render_unified_diff_rich(section))
            continue

        section_lines = _render_unified_diff_rich(section)
        remaining_budget = max_lines - len(rendered)
        if remaining_budget <= 0:
            omitted_lines += len(section_lines)
            omitted_files += 1
            continue

        if len(section_lines) <= remaining_budget:
            rendered.extend(section_lines)
            continue

        rendered.extend(section_lines[:remaining_budget])
        omitted_lines += len(section_lines) - remaining_budget
        omitted_files += 1 + max(0, len(sections) - idx - 1)
        for leftover in sections[idx + 1 :]:
            omitted_lines += len(_render_unified_diff_rich(leftover))
        break

    if omitted_files or omitted_lines:
        summary = f"\u2026 omitted {omitted_lines} diff line(s)"
        if omitted_files:
            summary += f" across {omitted_files} additional file(s)/section(s)"
        omit_text = Text(summary, style=_HUNK_COLOR)
        rendered.append(omit_text)

    return rendered


def render_inline_diff(
    tool_name: str,
    result: str | None,
    *,
    args: dict | None = None,
    snapshot: EditSnapshot | None = None,
) -> RenderableType | None:
    """Render an inline diff for a file-edit tool result.

    Returns a Rich renderable (Group of Text lines) or ``None`` if no
    diff is available.
    """
    if tool_name not in ("write", "edit"):
        return None
    if not _result_succeeded(result):
        return None

    diff = _diff_from_snapshot(snapshot)
    if not diff:
        return None

    rendered = _summarize_diff_sections(diff)
    if not rendered:
        return None

    header = Text()
    header.append("  \u250a review diff", style=f"dim {_META_COLOR}")
    return Group(header, *rendered)


__all__ = [
    "EditSnapshot",
    "capture_edit_snapshot",
    "render_inline_diff",
]
