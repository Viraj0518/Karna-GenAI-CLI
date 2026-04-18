"""Rich renderers for before/after file-edit diffs.

Three public entry points:

* :func:`render_unified_diff` — classic ``diff -u`` with Rich coloring.
* :func:`render_side_by_side` — two-column ``rich.table.Table`` diff.
* :func:`render_file_edit`    — wraps either style with a header showing
  the file path and (+added, -removed) stats.

Uses :mod:`difflib` for the actual diffing; pulls colors from
:data:`karna.tui.design_tokens.SEMANTIC` when available so the look stays
consistent with the rest of the TUI.
"""

from __future__ import annotations

import difflib
from typing import Literal

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

try:
    from karna.tui.design_tokens import SEMANTIC

    _ADDED = SEMANTIC.get("accent.success", "#7DCFA1")
    _REMOVED = SEMANTIC.get("accent.danger", "#E87C7C")
    _META = SEMANTIC.get("text.tertiary", "#5F6472")
    _HEADER = SEMANTIC.get("accent.brand", "#3C73BD")
except Exception:  # pragma: no cover - fallback
    _ADDED, _REMOVED, _META, _HEADER = "green", "red", "grey50", "cyan"


# --------------------------------------------------------------------------- #
#  Stats
# --------------------------------------------------------------------------- #


def _count_changes(old: str, new: str) -> tuple[int, int]:
    """Return ``(added_lines, removed_lines)`` between ``old`` and ``new``."""
    added = removed = 0
    for line in difflib.ndiff(old.splitlines(), new.splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


# --------------------------------------------------------------------------- #
#  Unified diff
# --------------------------------------------------------------------------- #


def render_unified_diff(
    old: str,
    new: str,
    *,
    path: str | None = None,
    context: int = 3,
) -> RenderableType:
    """Classic unified diff with Rich coloring.

    ``+`` lines get :data:`accent.success`, ``-`` lines :data:`accent.danger`,
    ``@@`` hunk markers are dimmed, and the file header is in the brand color.
    """
    from_label = f"a/{path}" if path else "before"
    to_label = f"b/{path}" if path else "after"
    lines = list(
        difflib.unified_diff(
            old.splitlines(keepends=False),
            new.splitlines(keepends=False),
            fromfile=from_label,
            tofile=to_label,
            n=context,
            lineterm="",
        )
    )

    body = Text()
    for raw in lines:
        if raw.startswith("+++") or raw.startswith("---"):
            body.append(raw + "\n", style=f"bold {_HEADER}")
        elif raw.startswith("@@"):
            body.append(raw + "\n", style=f"dim {_META}")
        elif raw.startswith("+"):
            body.append(raw + "\n", style=_ADDED)
        elif raw.startswith("-"):
            body.append(raw + "\n", style=_REMOVED)
        else:
            body.append(raw + "\n")
    if not lines:
        body.append("(no changes)\n", style=f"dim {_META}")
    return body


# --------------------------------------------------------------------------- #
#  Side-by-side diff
# --------------------------------------------------------------------------- #


def render_side_by_side(old: str, new: str, *, width: int = 80) -> Table:
    """Two-column diff rendered as a :class:`rich.table.Table`.

    Auto-wraps long lines (Rich handles the wrapping). The ``width`` kwarg
    sizes each column independently; the full table will be ``2*width + 4``
    columns wide including padding and borders.
    """
    table = Table(
        show_header=True,
        header_style=f"bold {_HEADER}",
        expand=False,
        pad_edge=False,
    )
    table.add_column("before", width=width, overflow="fold", style=_REMOVED)
    table.add_column("after", width=width, overflow="fold", style=_ADDED)

    matcher = difflib.SequenceMatcher(a=old.splitlines(), b=new.splitlines())
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old_chunk = old.splitlines()[i1:i2]
        new_chunk = new.splitlines()[j1:j2]
        height = max(len(old_chunk), len(new_chunk))
        for k in range(height):
            left = old_chunk[k] if k < len(old_chunk) else ""
            right = new_chunk[k] if k < len(new_chunk) else ""
            if tag == "equal":
                table.add_row(Text(left), Text(right))
            elif tag == "replace":
                table.add_row(Text(left, style=_REMOVED), Text(right, style=_ADDED))
            elif tag == "delete":
                table.add_row(Text(left, style=_REMOVED), Text(""))
            elif tag == "insert":
                table.add_row(Text(""), Text(right, style=_ADDED))
    return table


# --------------------------------------------------------------------------- #
#  File-edit wrapper
# --------------------------------------------------------------------------- #


def render_file_edit(
    path: str,
    old_content: str,
    new_content: str,
    *,
    mode: Literal["unified", "side-by-side"] = "unified",
) -> RenderableType:
    """Wrap a diff in a panel that shows the file path and +/- stats."""
    added, removed = _count_changes(old_content, new_content)
    header = Text()
    header.append(path, style=f"bold {_HEADER}")
    header.append("  ")
    header.append(f"+{added}", style=_ADDED)
    header.append(" ")
    header.append(f"-{removed}", style=_REMOVED)

    if mode == "side-by-side":
        body: RenderableType = render_side_by_side(old_content, new_content)
    else:
        body = render_unified_diff(old_content, new_content, path=path)

    return Panel(
        Group(header, body),
        border_style=_META,
        title=None,
        expand=False,
    )


__all__ = [
    "render_unified_diff",
    "render_side_by_side",
    "render_file_edit",
]
