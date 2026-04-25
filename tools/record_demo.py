"""Screen-record a demo session + extract frames + surface a report.

Two modes:

1. **--capture** — uses ffmpeg's ``gdigrab`` (Windows) or ``x11grab`` (Linux)
   to record the screen to ``_demo_recordings/<tag>.mp4`` for N seconds.
   If ffmpeg isn't on PATH, prints a fallback instruction (Win+Alt+R for
   Xbox Game Bar on Windows, ``xvfb-run`` + ``ffmpeg`` on Linux).

2. **--analyse <mp4>** — extracts 1 fps JPEG frames into
   ``<video_stem>_frames/``, runs ``tesseract`` OCR over the last frame
   to detect the final TUI state, then writes a markdown report with
   inline frame thumbnails + timeline.

Used by alpha to ingest user-submitted videos without asking for
follow-up (``let me ask why cann't you automate this checking?``).

Run::

    python tools/record_demo.py --analyse "C:\\Users\\12066\\Videos\\Captures\\Windows PowerShell 2026-04-21 01-07-30.mp4"
    python tools/record_demo.py --capture 30 --tag nellie_demo
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

_REC_DIR = Path(__file__).resolve().parent.parent / "_demo_recordings"


def _which(cmd: str) -> str | None:
    from shutil import which

    return which(cmd)


def capture(duration_s: int, tag: str) -> int:
    """Record the primary display for ``duration_s`` seconds.

    Writes to ``_demo_recordings/<tag>_<timestamp>.mp4`` at 25fps, fast
    preset. Returns the subprocess exit code, or 2 if ffmpeg is missing.
    """
    import time

    _REC_DIR.mkdir(parents=True, exist_ok=True)
    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg not on PATH. Windows fallback:")
        print("  Press Win+Alt+R to start Xbox Game Bar recording.")
        print("  Press Win+Alt+R again to stop.")
        print("  Video lands under ~/Videos/Captures/")
        return 2

    stamp = time.strftime("%Y%m%dT%H%M%S")
    out = _REC_DIR / f"{tag}_{stamp}.mp4"
    if sys.platform == "win32":
        grab = ["-f", "gdigrab", "-framerate", "25", "-i", "desktop"]
    elif sys.platform == "darwin":
        grab = ["-f", "avfoundation", "-framerate", "25", "-i", "1"]
    else:
        grab = ["-f", "x11grab", "-framerate", "25", "-i", os.environ.get("DISPLAY", ":0.0")]

    cmd = [
        ffmpeg,
        "-y",
        *grab,
        "-t",
        str(duration_s),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-pix_fmt",
        "yuv420p",
        str(out),
    ]
    print(f"recording → {out} for {duration_s}s")
    rc = subprocess.run(cmd).returncode
    print(f"exit={rc}")
    return rc


def extract_frames(mp4: Path, fps: int = 1) -> Path:
    """Write JPEG frames to ``<stem>_frames/``. Returns the directory."""
    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not on PATH — can't extract frames")
    out_dir = mp4.parent / f"{mp4.stem}_frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(mp4),
        "-vf",
        f"fps={fps}",
        "-q:v",
        "3",
        str(out_dir / "f_%03d.jpg"),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_dir


def _sorted_frames(dir_: Path) -> list[Path]:
    return sorted(dir_.glob("f_*.jpg"))


def _ocr(img: Path) -> str:
    """Return OCR text if tesseract is available, else ''."""
    tess = _which("tesseract")
    if not tess:
        return ""
    try:
        result = subprocess.run(
            [tess, str(img), "-", "-l", "eng", "--psm", "6"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def analyse(mp4: Path) -> int:
    """Extract frames + write markdown report. Returns 0 on success."""
    if not mp4.exists():
        print(f"not found: {mp4}", file=sys.stderr)
        return 1
    print(f"analysing {mp4.name}")
    frames_dir = extract_frames(mp4)
    frames = _sorted_frames(frames_dir)
    if not frames:
        print("no frames extracted", file=sys.stderr)
        return 1
    print(f"  extracted {len(frames)} frames → {frames_dir}")

    report = frames_dir / "REPORT.md"
    lines: list[str] = [
        f"# Frame analysis: `{mp4.name}`",
        "",
        f"- Frames: **{len(frames)}** @ 1fps → `{frames_dir.name}/`",
        f"- Total duration: ~{len(frames)}s",
        "",
        "## Timeline",
        "",
    ]
    # Sample every N-th frame up to a max of ~8 rows.
    sample_idx = [0, len(frames) // 4, len(frames) // 2, 3 * len(frames) // 4, len(frames) - 1]
    sample_idx = sorted(set(i for i in sample_idx if 0 <= i < len(frames)))
    for idx in sample_idx:
        f = frames[idx]
        text = _ocr(f)
        snippet = text.replace("\n", " ")[:160]
        lines.append(f"### Frame {idx + 1:03d} (`{f.name}`)")
        lines.append("")
        lines.append(f"![frame]({f.name})")
        if snippet:
            lines.append("")
            lines.append(f"> OCR: `{snippet}...`")
        lines.append("")

    # Detect a "silent turn" pattern: if final OCR has `> ` prompt but no
    # assistant reply markers, flag it.
    final_ocr = _ocr(frames[-1]).lower()
    flags: list[str] = []
    if ">" in final_ocr and "nellie" not in final_ocr and "thinking" not in final_ocr:
        flags.append("⚠ final frame has a user prompt but no assistant marker — possible silent turn")
    if "error" in final_ocr or "traceback" in final_ocr:
        flags.append("🔥 final frame mentions error/traceback")

    if flags:
        lines.extend(["## Flags", "", *[f"- {f}" for f in flags], ""])

    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"  report → {report}")
    return 0


def main(argv: Iterable[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="mode", required=True)

    cap = sub.add_parser("capture", help="record the screen with ffmpeg")
    cap.add_argument("duration", type=int, help="seconds to record")
    cap.add_argument("--tag", default="demo")

    an = sub.add_parser("analyse", help="extract frames + report from an mp4")
    an.add_argument("mp4", type=Path)

    args = p.parse_args(list(argv))

    if args.mode == "capture":
        return capture(args.duration, args.tag)
    if args.mode == "analyse":
        return analyse(args.mp4)
    return 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.exit(main(sys.argv[1:]))
