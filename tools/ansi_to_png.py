"""Render an ANSI-colored text stream to a PNG image.

Backends (tried in order, first-available wins):

    1. ``cairosvg``  — fast, pure-Python bindings around ``libcairo``.
       Reliable on Linux (apt install libcairo2) and macOS. Often broken
       on Windows because ``libcairo-2.dll`` is not on PATH.
    2. ``playwright`` — headless chromium rasterizes the SVG. Heavier but
       works anywhere python + chromium run. Good Windows fallback.
    3. ``ansi-text`` — no rasterization; writes the raw ANSI alongside
       the requested PNG path so callers can still diff. Signals via
       the return tuple which backend was used.

The pipeline is:

    ANSI  --(rich.Console.export_svg)-->  SVG  --(backend)-->  PNG

Usage::

    from tools.ansi_to_png import render_ansi_to_png
    backend = render_ansi_to_png(ansi_text, "out.png", width=100, title="demo")
    # backend is one of: "cairosvg", "playwright", "ansi-text"

Intentionally has no dependencies on the rest of the repo — callers pass
raw ANSI bytes so this module can be lifted into other projects.
"""

from __future__ import annotations

import io
import shutil
import sys
from pathlib import Path
from typing import Literal

from rich.console import Console
from rich.text import Text

Backend = Literal["cairosvg", "playwright", "ansi-text"]


def ansi_to_svg(ansi: str, *, width: int = 100, title: str = "karna-tui") -> str:
    """Convert ANSI text to an SVG string via ``rich.Console.export_svg``."""
    console = Console(
        file=io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
        width=width,
        record=True,
    )
    # ``Text.from_ansi`` re-parses SGR escapes into rich's styled text model,
    # which then exports cleanly to SVG. Printing the raw string would cause
    # rich to double-escape the sequences.
    console.print(Text.from_ansi(ansi))
    return console.export_svg(title=title, clear=True)


def _svg_to_png_cairosvg(svg: str, out_path: Path) -> bool:
    try:
        import cairosvg  # type: ignore[import-not-found]
    except Exception:
        return False
    try:
        cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=str(out_path))
        return True
    except Exception as exc:  # libcairo missing DLL, etc.
        print(f"[ansi_to_png] cairosvg backend failed: {exc}", file=sys.stderr)
        return False


def _svg_to_png_playwright(svg: str, out_path: Path) -> bool:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
    except Exception:
        return False
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;background:#0d1117;}</style>"
        f"</head><body>{svg}</body></html>"
    )
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1400, "height": 2000})
                page.set_content(html, wait_until="domcontentloaded")
                locator = page.locator("svg").first
                locator.screenshot(path=str(out_path), omit_background=False)
            finally:
                browser.close()
        return True
    except Exception as exc:
        print(f"[ansi_to_png] playwright backend failed: {exc}", file=sys.stderr)
        return False


def _write_ansi_sidecar(ansi: str, out_path: Path) -> None:
    """Fallback: write the raw ANSI next to the would-be PNG."""
    sidecar = out_path.with_suffix(".ansi")
    sidecar.write_text(ansi, encoding="utf-8")
    # Leave a zero-byte png marker so downstream path-exists checks stay sane.
    out_path.write_bytes(b"")


def render_ansi_to_png(
    ansi: str,
    out_path: str | Path,
    *,
    width: int = 100,
    title: str = "karna-tui",
    backend: Backend | None = None,
) -> Backend:
    """Render ``ansi`` to a PNG at ``out_path``. Returns the backend used."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    svg = ansi_to_svg(ansi, width=width, title=title)

    order: list[Backend]
    if backend is None:
        order = ["cairosvg", "playwright", "ansi-text"]
    else:
        order = [backend]

    for candidate in order:
        if candidate == "cairosvg" and _svg_to_png_cairosvg(svg, out_path):
            return "cairosvg"
        if candidate == "playwright" and _svg_to_png_playwright(svg, out_path):
            return "playwright"
        if candidate == "ansi-text":
            _write_ansi_sidecar(ansi, out_path)
            return "ansi-text"
    # Exhausted: force ansi-text
    _write_ansi_sidecar(ansi, out_path)
    return "ansi-text"


def detect_backend() -> Backend:
    """Return the backend that *would* be used without rendering anything."""
    try:
        import cairosvg  # type: ignore[import-not-found]  # noqa: F401
        # Probe a tiny render — cairocffi raises at call time, not import.
        cairosvg.svg2png(bytestring=b"<svg xmlns='http://www.w3.org/2000/svg' width='1' height='1'/>")
        return "cairosvg"
    except Exception:
        pass
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]  # noqa: F401
        # Don't actually launch chromium — just presence of the module is enough.
        if shutil.which("playwright") or True:
            return "playwright"
    except Exception:
        pass
    return "ansi-text"


if __name__ == "__main__":  # pragma: no cover - CLI convenience
    import argparse

    parser = argparse.ArgumentParser(description="Render stdin ANSI to a PNG.")
    parser.add_argument("--out", required=True, help="PNG output path")
    parser.add_argument("--width", type=int, default=100)
    parser.add_argument("--title", default="karna-tui")
    parser.add_argument("--backend", choices=["cairosvg", "playwright", "ansi-text"])
    args = parser.parse_args()

    ansi_in = sys.stdin.read()
    used = render_ansi_to_png(ansi_in, args.out, width=args.width, title=args.title, backend=args.backend)
    print(f"rendered with backend={used} → {args.out}")
