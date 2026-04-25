"""Extract frames from an MP4 at 1fps using ffmpeg.

Usage::

    python tools/extract_video_frames.py path/to/capture.mp4 [--fps=1]

Writes JPEGs to ``<video_stem>_frames/frame_000001.jpg`` next to the mp4.
Used by alpha to analyze user-submitted screen recordings automatically.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def extract_frames(video: Path, fps: float = 1.0, out_dir: Path | None = None) -> Path:
    if not video.exists():
        raise FileNotFoundError(video)
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")
    out_dir = out_dir or video.with_name(f"{video.stem}_frames")
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-i", str(video), "-vf", f"fps={fps}", str(out_dir / "frame_%06d.jpg")]
    subprocess.run(cmd, check=True)
    return out_dir


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("video", type=Path)
    p.add_argument("--fps", type=float, default=1.0)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)
    out = extract_frames(args.video, fps=args.fps, out_dir=args.out)
    print(f"frames → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
