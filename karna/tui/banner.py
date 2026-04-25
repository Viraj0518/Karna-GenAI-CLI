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

    _DIAMOND = getattr(_icons, "DIAMOND", None) or getattr(_icons, "diamond", None) or "\u25c6"
    _DOT = getattr(_icons, "DOT", None) or getattr(_icons, "dot", None) or "\u25cf"
except Exception:  # pragma: no cover - fallback
    _DIAMOND = "\u25c6"  # ◆
    _DOT = "\u25cf"  # ●


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
    # Match the status-bar de-dup logic so the banner doesn't show
    # "openrouter/openrouter/auto" when the stored model already includes
    # the provider prefix (OpenRouter stores "<org>/<model>", which Karna
    # sometimes re-saves as "openrouter/openrouter/<org>/<model>").
    _m = config.active_model or ""
    if _m.startswith(f"{config.active_provider}/"):
        model_label = _m
    else:
        model_label = f"{config.active_provider}/{_m}"
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

    # ── ASCII art banner ──────────────────────────────────────────────────
    # Gradient: cyan (#87CEEB) → brand blue (#3C73BD) top to bottom
    art_lines = [
        r"  ███╗   ██╗███████╗██╗     ██╗     ██╗███████╗",
        r"  ████╗  ██║██╔════╝██║     ██║     ██║██╔════╝",
        r"  ██╔██╗ ██║█████╗  ██║     ██║     ██║█████╗  ",
        r"  ██║╚██╗██║██╔══╝  ██║     ██║     ██║██╔══╝  ",
        r"  ██║ ╚████║███████╗███████╗███████╗██║███████╗",
        r"  ╚═╝  ╚═══╝╚══════╝╚══════╝╚══════╝╚═╝╚══════╝",
    ]
    # Apply gradient from cyan to brand blue across lines
    grad_colors = [cyan, cyan, "#5A8FCC", "#5A8FCC", brand, brand]
    console.print()
    for line, color in zip(art_lines, grad_colors):
        console.print(Text(line, style=f"bold {color}"))

    # ── Version + status on same line after art ─────────────────────────
    width = max(console.size.width, 60)
    ver_text = f"  v{__version__}"
    status_text = f"{_DOT} ready  "
    header = Text()
    header.append(ver_text, style=f"dim {t_tertiary}")
    pad = max(width - len(ver_text) - len(status_text), 2)
    header.append(" " * pad)
    header.append(status_text, style=success)

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

    # ── Fortune / daily message ─────────────────────────────────────────
    try:
        from karna.tui.fortunes import pick_fortune

        fortune = pick_fortune()
    except Exception:  # pragma: no cover - defensive
        fortune = None

    # ── Emit with generous vertical breathing room ───────────────────────
    console.print()
    console.print(header)
    console.print(Rule(style=divider))
    console.print(info)
    console.print()
    if fortune:
        fortune_text = Text("  ")
        fortune_text.append("\U0001f52e ", style=f"dim {t_tertiary}")
        fortune_text.append(fortune, style=f"italic dim {t_tertiary}")
        console.print(fortune_text)
    console.print(hint)
    console.print()


__all__ = ["print_banner"]
