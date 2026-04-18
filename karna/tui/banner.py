"""Startup banner printed when the REPL launches.

Design: minimal header with a divider and a one-line status row.
Semantic tokens from ``karna.tui.design_tokens`` drive all colors —
no raw hex literals live here.

Layout (Option B — minimal header with divider):

    <blank>
      diamond  nellie vX.Y.Z                                   dot  ready
      ------------------------------------------------------------
      <model> - <N> tools - <workspace-name> (<project-kind>)

      Type a prompt or /help - karna's AI assistant
    <blank>

The middle line gives a glance at state; the hint line beneath is dim
and names the product ("karna") so humans know whose agent they're
talking to.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from karna import __version__
from karna.config import KarnaConfig
from karna.tui.design_tokens import SEMANTIC

# ── Optional icons (the icons module is authored by another agent; be
#    resilient if it hasn't landed yet). ────────────────────────────────
try:  # pragma: no cover - trivial import guard
    from karna.tui import icons as _icons  # type: ignore
    _DIAMOND = getattr(_icons, "DIAMOND", None) or getattr(_icons, "diamond", None) or "\u25C6"
    _DOT = getattr(_icons, "DOT", None) or getattr(_icons, "dot", None) or "\u25CF"
except Exception:  # pragma: no cover - fallback
    _DIAMOND = "\u25C6"  # ◆
    _DOT = "\u25CF"      # ●


# --------------------------------------------------------------------------- #
#  Workspace detection
# --------------------------------------------------------------------------- #

_PROJECT_MARKERS: tuple[tuple[str, str], ...] = (
    ("pyproject.toml", "python"),
    ("setup.py", "python"),
    ("requirements.txt", "python"),
    ("package.json", "node"),
    ("Cargo.toml", "rust"),
    ("go.mod", "go"),
    ("pom.xml", "java"),
    ("build.gradle", "java"),
    ("Gemfile", "ruby"),
    ("composer.json", "php"),
)


def _detect_project_kind(cwd: Path) -> str | None:
    """Return a short project-type hint (``python``, ``node``, ...) or ``None``."""
    try:
        entries = {p.name for p in cwd.iterdir() if p.is_file()}
    except OSError:
        return None
    for marker, kind in _PROJECT_MARKERS:
        if marker in entries:
            return kind
    return None


def _detect_git(cwd: Path) -> bool:
    return (cwd / ".git").exists()


def _workspace_label(cwd: Path) -> str:
    """Return a compact workspace description, e.g. ``my-proj (python - git)``."""
    name = cwd.name or str(cwd)
    tags: list[str] = []
    kind = _detect_project_kind(cwd)
    if kind:
        tags.append(kind)
    if _detect_git(cwd):
        tags.append("git")
    if tags:
        return f"{name} ({' - '.join(tags)})"
    return name


# --------------------------------------------------------------------------- #
#  Public API
# --------------------------------------------------------------------------- #

def print_banner(
    console: Console,
    config: KarnaConfig,
    tool_names: Sequence[str] = (),
) -> None:
    """Render the startup banner to *console*.

    Public signature is unchanged; only the visual output differs.
    """
    model_label = f"{config.active_provider}/{config.active_model}"
    tool_count = len(tool_names)

    brand = SEMANTIC.get("accent.brand", "#3C73BD")
    cyan = SEMANTIC.get("accent.cyan", "#87CEEB")
    success = SEMANTIC.get("accent.success", "#7DCFA1")
    t_primary = SEMANTIC.get("text.primary", "#E6E8EC")
    t_secondary = SEMANTIC.get("text.secondary", "#A0A4AD")
    t_tertiary = SEMANTIC.get("text.tertiary", "#5F6472")
    divider = SEMANTIC.get("divider", "#2A2F38")

    try:
        cwd = Path(os.getcwd())
    except OSError:
        cwd = Path(".")
    workspace = _workspace_label(cwd)

    # ── Header line: diamond + wordmark + version ...dot + status ────────
    header = Text()
    header.append("  ")
    header.append(f"{_DIAMOND} ", style=brand)
    header.append("nellie", style=f"bold {cyan}")
    header.append(f"  v{__version__}", style=f"dim {t_tertiary}")
    # right-side status, pushed toward the edge by the Rule width below.
    status = Text()
    status.append(f"{_DOT} ready  ", style=success)

    # Build a two-part line that fills console width: left grows, right is
    # right-aligned. Rich ``Text`` doesn't natively right-align in a line
    # so we pad manually using the console width.
    width = max(console.size.width, 60)
    used = len(header.plain) + len(status.plain)
    pad = max(width - used, 2)
    header.append(" " * pad)
    header.append_text(status)

    # ── Status row: model - tools - workspace ────────────────────────────
    info = Text("  ")
    info.append(model_label, style=t_primary)
    info.append("  -  ", style=t_tertiary)
    info.append(f"{tool_count} loaded", style=t_secondary)
    info.append(" tools", style=t_tertiary)
    info.append("  -  ", style=t_tertiary)
    info.append(workspace, style=t_secondary)

    # ── Hint row: invite + karna signature ───────────────────────────────
    hint = Text("  ")
    hint.append("Type a prompt or ", style=t_tertiary)
    hint.append("/help", style=f"bold {brand}")
    hint.append("  -  ", style=t_tertiary)
    hint.append("karna's AI assistant", style=f"italic {t_tertiary}")

    # ── Emit with generous vertical breathing room ───────────────────────
    console.print()
    console.print(header)
    console.print(Rule(style=divider))
    console.print(info)
    console.print()
    console.print(hint)
    console.print()


__all__ = ["print_banner"]
