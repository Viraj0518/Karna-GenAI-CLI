"""Port of upstream reference's diff / file-edit visuals to Rich renderables.

Source components (paths relative to upstream repo root ``the upstream project/components/``):

* ``StructuredDiff.tsx``            -> :func:`render_structured_diff` (hunk render)
* ``StructuredDiffList.tsx``        -> list dispatch in :func:`render_structured_diff`
* ``StructuredDiff/colorDiff.ts``   -> colour lookup (fallback path only here;
                                        the NAPI ``color-diff`` module is
                                        node-only so syntax highlighting is
                                        intentionally not ported)
* ``StructuredDiff/Fallback.tsx``   -> prefix / gutter / dim behaviour
* ``FileEditToolDiff.tsx``          -> dashed frame wrapper
* ``FileEditToolUpdatedMessage.tsx``-> :func:`render_file_edit_accepted`
                                        ("Added N lines, removed M lines" header)
* ``FileEditToolUseRejectedMessage.tsx`` -> :func:`render_file_edit_rejected`
                                        ("User rejected update to <path>")
* ``FallbackToolUseErrorMessage.tsx``-> :func:`render_tool_error`
* ``FallbackToolUseRejectedMessage.tsx`` + ``NotebookEditToolUseRejectedMessage.tsx``
                                    -> :func:`render_tool_rejected`
* ``FilePathLink.tsx``              -> :func:`render_file_path_link`
                                        (``pathToFileURL`` -> OSC-8 via Rich)

**Nellie skin deltas** (everything else mirrors upstream 1:1):

* upstream's theme-aware ``diffAdded`` / ``diffRemoved`` / ``diffAddedWord`` /
  ``diffRemovedWord`` / ``diffAddedDimmed`` / ``diffRemovedDimmed`` tokens are
  sourced from ``karna.tui.design_tokens.SEMANTIC`` where an equivalent exists
  (``accent.success`` / ``accent.danger`` / ``text.tertiary``), and fall back
  to the exact RGB triples from upstream's dark theme (``utils/theme.ts`` lines
  141-146) when a skin token is missing.
* The "tool result" branch glyph is Nellie's ``⎿`` from
  :data:`karna.tui.hermes_display.NELLIE_TOOL_RESULT_GLYPH`, which matches
  upstream's ``L/ellipsis-between-hunks`` visual.
* Brand accent is Nellie's ``#3C73BD`` (pulled via ``accent.brand``) for the
  file-path header, matching the spot upstream uses its theme's ``subtle`` token.

Library-only module: callers render with a ``rich.console.Console`` — this
file imports nothing from ``hermes_repl``.
"""

from __future__ import annotations

import difflib
import os
from typing import Sequence
from urllib.parse import quote as _urlquote

from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

# --------------------------------------------------------------------------- #
#  Colour resolution — follows upstream's token names, falls back to Nellie tokens,
#  and finally to the RGB values baked into upstream's dark theme (utils/theme.ts).
# --------------------------------------------------------------------------- #


def _resolve_palette() -> dict[str, str]:
    """Return a mapping of upstream token names to Rich colour strings.

    Prefers Nellie's :data:`karna.tui.design_tokens.SEMANTIC` when the
    equivalent token exists; otherwise uses the literal RGB triples copied
    out of upstream's dark theme (``utils/theme.ts``).
    """
    palette = {
        # upstream dark theme (utils/theme.ts:141-146)
        "diffAdded": "rgb(105,219,124)",  # line bg for added lines
        "diffRemoved": "rgb(255,168,180)",  # line bg for removed lines
        "diffAddedDimmed": "rgb(199,225,203)",
        "diffRemovedDimmed": "rgb(253,210,216)",
        "diffAddedWord": "rgb(47,157,68)",  # word-level highlight
        "diffRemovedWord": "rgb(209,69,75)",
        # Chrome
        "subtle": "grey50",  # upstream uses theme 'subtle' for headers
        "brand": "#3C73BD",  # Nellie brand override
        "error": "red",
        "dim": "grey58",
    }
    try:
        from karna.tui.design_tokens import SEMANTIC  # lazy so tests without Nellie can stub it
    except Exception:  # pragma: no cover
        return palette
    # Map upstream tokens onto Nellie tokens where an equivalent exists. upstream's line
    # colours are backgrounds on white text; Nellie's dark-first palette uses
    # accent.success / accent.danger as foreground colours, so we use them as
    # foreground on the added/removed lines (matches upstream's "ansi:green" /
    # "ansi:red" light-mode branch in utils/theme.ts:223-224).
    palette["diffAdded"] = SEMANTIC.get("accent.success", palette["diffAdded"])
    palette["diffRemoved"] = SEMANTIC.get("accent.danger", palette["diffRemoved"])
    palette["diffAddedWord"] = SEMANTIC.get("accent.success", palette["diffAddedWord"])
    palette["diffRemovedWord"] = SEMANTIC.get("accent.danger", palette["diffRemovedWord"])
    palette["subtle"] = SEMANTIC.get("text.tertiary", palette["subtle"])
    palette["brand"] = SEMANTIC.get("accent.brand", palette["brand"])
    palette["error"] = SEMANTIC.get("accent.danger", palette["error"])
    palette["dim"] = SEMANTIC.get("text.tertiary", palette["dim"])
    return palette


_PALETTE = _resolve_palette()


# Nellie's tool-result branch glyph; matches upstream's visual break between hunks.
try:
    from karna.tui.hermes_display import NELLIE_TOOL_RESULT_GLYPH
except Exception:  # pragma: no cover
    NELLIE_TOOL_RESULT_GLYPH = "\u23bf"


# --------------------------------------------------------------------------- #
#  Structured diff — mirrors upstream's StructuredDiff + StructuredDiffFallback
# --------------------------------------------------------------------------- #


def _count_digits(n: int) -> int:
    return max(1, len(str(max(n, 1))))


def _build_hunks(
    old: str,
    new: str,
    *,
    context: int = 3,
) -> list[dict]:
    """Return a list of hunks shaped like upstream's ``StructuredPatchHunk``.

    We invoke :func:`difflib.unified_diff` and chunk on ``@@`` markers so the
    per-hunk rendering (with line-number gutter, ``+``/``-``/`` `` prefixes)
    matches upstream's ``StructuredDiffFallback`` one for one.
    """
    diff = list(
        difflib.unified_diff(
            old.splitlines(keepends=False),
            new.splitlines(keepends=False),
            n=context,
            lineterm="",
        )
    )
    # Drop the leading "--- before" / "+++ after" envelope lines.
    diff = [ln for ln in diff if not ln.startswith(("---", "+++"))]

    hunks: list[dict] = []
    current: dict | None = None
    for raw in diff:
        if raw.startswith("@@"):
            # Example: "@@ -1,3 +1,4 @@"
            try:
                _, old_range, new_range, *_ = raw.split()
                old_start = int(old_range.split(",")[0].lstrip("-"))
                old_lines = int(old_range.split(",")[1]) if "," in old_range else 1
                new_start = int(new_range.split(",")[0].lstrip("+"))
                new_lines = int(new_range.split(",")[1]) if "," in new_range else 1
            except (ValueError, IndexError):  # pragma: no cover
                old_start = new_start = 1
                old_lines = new_lines = 0
            current = {
                "header": raw,
                "oldStart": old_start,
                "oldLines": old_lines,
                "newStart": new_start,
                "newLines": new_lines,
                "lines": [],
            }
            hunks.append(current)
        elif current is not None:
            current["lines"].append(raw)
    return hunks


def _render_hunk(hunk: dict, palette: dict[str, str], *, dim: bool) -> Text:
    """Render a single hunk — line-number gutter + prefix + payload.

    upstream (``StructuredDiffFallback.tsx``) right-aligns the line number in a
    gutter sized to ``max(oldStart+oldLines, newStart+newLines).toString().length``
    followed by the marker ("+", "-", " "). We mirror that exactly; Rich
    handles the width via ``str.rjust``.
    """
    gutter_width = _count_digits(
        max(
            hunk["oldStart"] + hunk["oldLines"] - 1,
            hunk["newStart"] + hunk["newLines"] - 1,
            1,
        )
    )

    old_i = hunk["oldStart"]
    new_i = hunk["newStart"]

    body = Text()
    for raw in hunk["lines"]:
        if not raw:
            # Occasional empty trailing line from difflib; skip like upstream does.
            continue
        marker = raw[:1]
        payload = raw[1:]
        if marker == "+":
            style = palette["diffAddedDimmed"] if dim else palette["diffAdded"]
            num = str(new_i).rjust(gutter_width)
            new_i += 1
            body.append(f"{num} +{payload}\n", style=style)
        elif marker == "-":
            style = palette["diffRemovedDimmed"] if dim else palette["diffRemoved"]
            num = str(old_i).rjust(gutter_width)
            old_i += 1
            body.append(f"{num} -{payload}\n", style=style)
        else:  # context line (" ")
            num = str(new_i).rjust(gutter_width)
            old_i += 1
            new_i += 1
            body.append(f"{num}  {payload}\n", style=f"dim {palette['dim']}")
    return body


def render_structured_diff(
    old: str,
    new: str,
    *,
    path: str | None = None,
) -> RenderableType:
    """Render a upstream-style structured diff for ``old -> new``.

    Mirrors ``StructuredDiffList`` + ``StructuredDiff`` + ``Fallback``:

    * File-path header in the brand colour (upstream's ``subtle`` → Nellie brand).
    * One section per hunk with a line-number gutter, ``+``/``-``/`` `` prefix,
      and coloured payload (green additions, red deletions, dim context).
    * Between hunks upstream renders ``...`` as a dim separator; we render
      ``⎿ ...`` so it blends with Nellie's tool-result branch glyph.
    """
    palette = _PALETTE
    hunks = _build_hunks(old, new)

    pieces: list[RenderableType] = []
    if path:
        header = Text()
        header.append(path, style=f"bold {palette['brand']}")
        pieces.append(header)

    if not hunks:
        msg = Text("(no changes)", style=f"dim {palette['dim']}")
        pieces.append(msg)
        return Group(*pieces)

    for idx, hunk in enumerate(hunks):
        if idx > 0:
            pieces.append(Text(f"{NELLIE_TOOL_RESULT_GLYPH} ...", style=f"dim {palette['dim']}"))
        # upstream shows the hunk header (``@@ -a,b +c,d @@``) in a dimmer colour;
        # we match by rendering in ``palette['subtle']`` with ``dim``.
        pieces.append(Text(hunk["header"], style=f"dim {palette['subtle']}"))
        pieces.append(_render_hunk(hunk, palette, dim=False))

    return Group(*pieces)


# --------------------------------------------------------------------------- #
#  File-edit framing — accepted / rejected
# --------------------------------------------------------------------------- #


def _count_plus_minus(diff_hunks: Sequence[dict]) -> tuple[int, int]:
    adds = sum(1 for h in diff_hunks for ln in h["lines"] if ln.startswith("+"))
    rems = sum(1 for h in diff_hunks for ln in h["lines"] if ln.startswith("-"))
    return adds, rems


def render_file_edit_accepted(
    path: str,
    diff_renderable: RenderableType,
) -> RenderableType:
    """Wrap an accepted edit in upstream's "Added N lines, removed M lines" framing.

    Mirrors ``FileEditToolUpdatedMessage.tsx`` (dashed top/bottom border via
    :class:`rich.panel.Panel` — upstream uses ``borderStyle="dashed"`` with left
    and right borders suppressed; Rich doesn't support partial borders so we
    approximate with the default box and dim the border colour).
    """
    palette = _PALETTE
    header = Text()
    header.append(path, style=f"bold {palette['brand']}")
    return Panel(
        Group(header, diff_renderable),
        border_style=f"dim {palette['subtle']}",
        title=None,
        expand=False,
    )


def render_file_edit_rejected(
    path: str,
    old: str,
    new: str,
) -> RenderableType:
    """Render "User rejected update to <path>" plus the WOULD-have-applied diff.

    Mirrors ``FileEditToolUseRejectedMessage.tsx`` — the header line reads
    ``User rejected update to <bold path>`` in upstream's ``subtle`` colour, and
    the diff below is rendered with ``dim=true``. We faithfully dim both.
    """
    palette = _PALETTE
    header = Text()
    header.append("User rejected update to ", style=palette["subtle"])
    header.append(path, style=f"bold {palette['subtle']}")

    hunks = _build_hunks(old, new)
    body_parts: list[RenderableType] = [header]
    if not hunks:
        body_parts.append(Text("(no changes)", style=f"dim {palette['dim']}"))
    else:
        for idx, hunk in enumerate(hunks):
            if idx > 0:
                body_parts.append(
                    Text(
                        f"{NELLIE_TOOL_RESULT_GLYPH} ...",
                        style=f"dim {palette['dim']}",
                    )
                )
            body_parts.append(Text(hunk["header"], style=f"dim {palette['subtle']}"))
            body_parts.append(_render_hunk(hunk, palette, dim=True))
    return Padding(Group(*body_parts), (0, 0, 0, 2))


# --------------------------------------------------------------------------- #
#  Fallback error / rejected visuals
# --------------------------------------------------------------------------- #


def render_tool_error(tool_name: str, error_msg: str) -> RenderableType:
    """upstream's ``FallbackToolUseErrorMessage`` visual.

    Source strips ``<error>`` tags and ``<tool_use_error>`` wrappers, then
    prefixes with "Error: " if absent. We preserve the stripping/prefixing
    rules and paint in ``error`` colour (Rich inherits terminal red).
    """
    palette = _PALETTE
    trimmed = (error_msg or "").strip()
    # Strip the XML tags upstream filters out (utils/messages.extractTag is a no-op
    # when the tag is absent, so stripping is always safe).
    for tag in ("<error>", "</error>", "<tool_use_error>", "</tool_use_error>"):
        trimmed = trimmed.replace(tag, "")
    trimmed = trimmed.strip()
    if not trimmed:
        trimmed = "Tool execution failed"
    if not (trimmed.startswith("Error: ") or trimmed.startswith("Cancelled: ")):
        trimmed = f"Error: {trimmed}"

    line = Text()
    line.append(f"{NELLIE_TOOL_RESULT_GLYPH} ", style=f"dim {palette['dim']}")
    line.append(f"[{tool_name}] ", style=f"bold {palette['brand']}")
    line.append(trimmed, style=palette["error"])
    return line


def render_tool_rejected(tool_name: str, reason: str) -> RenderableType:
    """upstream's ``FallbackToolUseRejectedMessage`` visual.

    Source renders ``<InterruptedByUser />`` which prints
    "Interrupted by user" in the theme's ``secondaryText`` colour. We
    combine with ``reason`` (upstream doesn't accept a reason; we extend it as
    the task spec requires).
    """
    palette = _PALETTE
    line = Text()
    line.append(f"{NELLIE_TOOL_RESULT_GLYPH} ", style=f"dim {palette['dim']}")
    line.append(f"[{tool_name}] ", style=f"bold {palette['brand']}")
    line.append("User rejected tool use", style=palette["subtle"])
    if reason:
        line.append(": ", style=palette["subtle"])
        line.append(reason, style=f"italic {palette['subtle']}")
    return line


# --------------------------------------------------------------------------- #
#  File-path link — OSC-8 hyperlink, like upstream's FilePathLink
# --------------------------------------------------------------------------- #


def _path_to_file_url(path: str) -> str:
    """Mirror node's ``url.pathToFileURL`` well enough for OSC-8.

    Windows paths (``C:\\foo``) become ``file:///C:/foo``; POSIX paths use a
    single leading slash from the input path itself.
    """
    abs_path = os.path.abspath(path)
    # Normalise to forward slashes so the URI is valid.
    norm = abs_path.replace("\\", "/")
    if len(norm) >= 2 and norm[1] == ":":
        # Windows drive letter
        return "file:///" + _urlquote(norm, safe="/:")
    if not norm.startswith("/"):
        norm = "/" + norm
    return "file://" + _urlquote(norm, safe="/:")


def render_file_path_link(path: str) -> Text:
    """Return a Rich :class:`Text` with an OSC-8 hyperlink to *path*.

    Mirrors ``FilePathLink.tsx`` which emits ink's ``<Link>`` (OSC-8) using
    ``url.pathToFileURL(filePath).href``. Rich's ``Text.stylize(link=...)``
    produces the same escape sequence when the terminal supports it; Rich
    degrades gracefully otherwise, matching ink's behaviour.
    """
    palette = _PALETTE
    url = _path_to_file_url(path)
    txt = Text(path, style=palette["brand"])
    # Rich applies the link as part of the style span, emitting OSC-8 on
    # terminals that support it. Keep the path itself as the visible text.
    try:
        txt.stylize(f"link {url}")
    except Exception:  # pragma: no cover - very old Rich
        pass
    return txt


__all__ = [
    "render_structured_diff",
    "render_file_edit_accepted",
    "render_file_edit_rejected",
    "render_tool_error",
    "render_tool_rejected",
    "render_file_path_link",
]
