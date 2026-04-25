"""Visual-regression harness for karna's TUI.

Drives ``tools/tui_screenshot.py`` for each of the 5 scenarios, renders
the ANSI to PNG via ``tools/ansi_to_png.py``, and compares each result
against a committed baseline in ``tests/visual_baselines/``.

Modes::

    python tools/visual_regression.py --mode=baseline   # (re)write baselines
    python tools/visual_regression.py --mode=check      # compare, exit !=0 on regression

On check, writes a Markdown report to ``_visual_diff/REPORT.md`` with
baseline/current/diff images inline. Exit code 0 iff every scenario
diffs <= --threshold fraction of pixels.

Backend selection mirrors ``tools/ansi_to_png.py``. When the fallback
``ansi-text`` backend is in use we skip pixel math and text-diff the
raw ANSI instead — still a regression test, just less pretty.
"""

from __future__ import annotations

import argparse
import difflib
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# Make sibling imports work when run as a script.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ansi_to_png import Backend, detect_backend, render_ansi_to_png  # noqa: E402
from tools.tui_screenshot import TURNS  # noqa: E402

BASELINE_DIR = ROOT / "tests" / "visual_baselines"
OUT_DIR = ROOT / "_visual_diff"

SCENARIOS = list(TURNS.keys())  # greeting, planning, research, brainstorm, tool


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    diff_fraction: float
    message: str
    baseline_path: Path
    current_path: Path
    diff_path: Path | None


def _capture_ansi(name: str) -> str:
    label, fn = TURNS[name]
    return fn()


def _write_png(name: str, ansi: str, out_path: Path, backend_hint: Backend | None) -> Backend:
    return render_ansi_to_png(
        ansi,
        out_path,
        width=100,
        title=f"karna-{name}",
        backend=backend_hint,
    )


def _pixel_diff(baseline: Path, current: Path, diff_out: Path) -> tuple[float, str]:
    """Return (fraction_different, human_message). Writes a diff PNG."""
    from PIL import Image, ImageChops

    a = Image.open(baseline).convert("RGB")
    b = Image.open(current).convert("RGB")
    if a.size != b.size:
        # Normalize sizes by padding the smaller — large size diffs still
        # produce a big fraction but we avoid ValueError.
        w = max(a.size[0], b.size[0])
        h = max(a.size[1], b.size[1])
        canvas_a = Image.new("RGB", (w, h), "black")
        canvas_b = Image.new("RGB", (w, h), "black")
        canvas_a.paste(a, (0, 0))
        canvas_b.paste(b, (0, 0))
        a, b = canvas_a, canvas_b
    diff = ImageChops.difference(a, b)
    bbox = diff.getbbox()
    if bbox is None:
        diff_out.parent.mkdir(parents=True, exist_ok=True)
        diff.save(diff_out)
        return 0.0, "identical"
    # Fraction of non-zero pixels
    bands = diff.split()
    total_pixels = a.size[0] * a.size[1]
    changed = 0
    for band in bands:
        changed = max(changed, sum(1 for px in band.getdata() if px != 0))
    fraction = changed / total_pixels
    diff_out.parent.mkdir(parents=True, exist_ok=True)
    diff.save(diff_out)
    return fraction, f"{changed}/{total_pixels} pixels differ ({fraction * 100:.2f}%)"


def _text_diff(baseline: Path, current: Path, diff_out: Path) -> tuple[float, str]:
    base_text = baseline.read_text(encoding="utf-8")
    cur_text = current.read_text(encoding="utf-8")
    if base_text == cur_text:
        diff_out.write_text("identical\n", encoding="utf-8")
        return 0.0, "identical"
    diff_lines = list(
        difflib.unified_diff(
            base_text.splitlines(keepends=True),
            cur_text.splitlines(keepends=True),
            fromfile=str(baseline),
            tofile=str(current),
            n=2,
        )
    )
    diff_out.write_text("".join(diff_lines), encoding="utf-8")
    # Fraction = changed-line count / total-line count of baseline.
    base_lines = base_text.splitlines() or [""]
    changed = sum(
        1 for line in diff_lines if line.startswith(("+ ", "- ", "+", "-")) and not line.startswith(("+++", "---"))
    )
    fraction = min(1.0, changed / max(1, len(base_lines)))
    return fraction, f"{changed} line(s) differ ({fraction * 100:.2f}% of baseline lines)"


def run_baseline(backend_hint: Backend | None) -> list[ScenarioResult]:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    results: list[ScenarioResult] = []
    effective_backend = backend_hint or detect_backend()
    for name in SCENARIOS:
        ansi = _capture_ansi(name)
        # For ansi-text backend, baseline is an .ansi file; else .png.
        if effective_backend == "ansi-text":
            base_path = BASELINE_DIR / f"{name}.ansi"
            base_path.write_text(ansi, encoding="utf-8")
            # Create a zero-byte marker PNG so artifact paths are consistent.
            (BASELINE_DIR / f"{name}.png").write_bytes(b"")
        else:
            base_path = BASELINE_DIR / f"{name}.png"
            _write_png(name, ansi, base_path, backend_hint=effective_backend)
            # Also stash the raw ANSI so a CI with a different backend can
            # still run the text-diff fallback against a known-good snapshot.
            (BASELINE_DIR / f"{name}.ansi").write_text(ansi, encoding="utf-8")
        results.append(
            ScenarioResult(
                name=name,
                passed=True,
                diff_fraction=0.0,
                message=f"baseline written ({effective_backend})",
                baseline_path=base_path,
                current_path=base_path,
                diff_path=None,
            )
        )
    return results


def run_check(threshold: float, backend_hint: Backend | None) -> tuple[list[ScenarioResult], Backend]:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    effective_backend = backend_hint or detect_backend()
    results: list[ScenarioResult] = []

    for name in SCENARIOS:
        ansi = _capture_ansi(name)

        png_baseline = BASELINE_DIR / f"{name}.png"
        ansi_baseline = BASELINE_DIR / f"{name}.ansi"

        # Prefer PNG diff when both sides can do pixels.
        can_pixel = (
            effective_backend in ("cairosvg", "playwright")
            and png_baseline.exists()
            and png_baseline.stat().st_size > 0
        )
        if can_pixel:
            cur_png = OUT_DIR / f"{name}.current.png"
            _write_png(name, ansi, cur_png, backend_hint=effective_backend)
            diff_png = OUT_DIR / f"{name}.diff.png"
            fraction, msg = _pixel_diff(png_baseline, cur_png, diff_png)
            passed = fraction <= threshold
            results.append(
                ScenarioResult(
                    name=name,
                    passed=passed,
                    diff_fraction=fraction,
                    message=msg,
                    baseline_path=png_baseline,
                    current_path=cur_png,
                    diff_path=diff_png,
                )
            )
        else:
            # Text fallback
            if not ansi_baseline.exists():
                results.append(
                    ScenarioResult(
                        name=name,
                        passed=False,
                        diff_fraction=1.0,
                        message=f"no baseline at {ansi_baseline}",
                        baseline_path=ansi_baseline,
                        current_path=OUT_DIR / f"{name}.current.ansi",
                        diff_path=None,
                    )
                )
                continue
            cur_ansi = OUT_DIR / f"{name}.current.ansi"
            cur_ansi.write_text(ansi, encoding="utf-8")
            diff_txt = OUT_DIR / f"{name}.diff.txt"
            fraction, msg = _text_diff(ansi_baseline, cur_ansi, diff_txt)
            passed = fraction <= threshold
            results.append(
                ScenarioResult(
                    name=name,
                    passed=passed,
                    diff_fraction=fraction,
                    message=msg,
                    baseline_path=ansi_baseline,
                    current_path=cur_ansi,
                    diff_path=diff_txt,
                )
            )

    _write_report(results, effective_backend, threshold)
    return results, effective_backend


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(OUT_DIR.parent)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _write_report(results: list[ScenarioResult], backend: Backend, threshold: float) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Visual regression report\n")
    lines.append(f"- Backend: `{backend}`\n")
    lines.append(f"- Threshold: {threshold * 100:.2f}% pixel/line difference\n")
    all_pass = all(r.passed for r in results)
    lines.append(f"- Overall: {'PASS' if all_pass else 'FAIL'}\n\n")

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        lines.append(f"## `{r.name}` — {status} ({r.message})\n\n")
        if r.baseline_path.suffix == ".png" and r.current_path.suffix == ".png":
            lines.append(f"Baseline: `{_rel(r.baseline_path)}`\n\n")
            lines.append(f"![baseline-{r.name}](../{_rel(r.baseline_path)})\n\n")
            lines.append(f"Current: `{_rel(r.current_path)}`\n\n")
            lines.append(f"![current-{r.name}](../{_rel(r.current_path)})\n\n")
            if r.diff_path is not None:
                lines.append(f"Diff: `{_rel(r.diff_path)}`\n\n")
                lines.append(f"![diff-{r.name}](../{_rel(r.diff_path)})\n\n")
        else:
            lines.append(f"Baseline: `{_rel(r.baseline_path)}`\n\n")
            lines.append(f"Current: `{_rel(r.current_path)}`\n\n")
            if r.diff_path is not None:
                lines.append(f"Diff: `{_rel(r.diff_path)}`\n\n")
                try:
                    lines.append("```diff\n")
                    lines.append(r.diff_path.read_text(encoding="utf-8")[:4000])
                    lines.append("\n```\n\n")
                except Exception:
                    pass

    (OUT_DIR / "REPORT.md").write_text("".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TUI visual regression harness.")
    parser.add_argument("--mode", choices=["baseline", "check"], default="check")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.02,
        help="Max fraction of pixels/lines that may differ (default 0.02 = 2%%).",
    )
    parser.add_argument("--backend", choices=["cairosvg", "playwright", "ansi-text"], default=None)
    args = parser.parse_args(argv)

    if args.mode == "baseline":
        results = run_baseline(args.backend)
        print(f"Wrote {len(results)} baselines to {BASELINE_DIR}")
        return 0

    results, backend = run_check(args.threshold, args.backend)
    print(f"Visual regression — backend={backend}, threshold={args.threshold * 100:.2f}%")
    fail = 0
    for r in results:
        tag = "PASS" if r.passed else "FAIL"
        print(f"  [{tag}] {r.name}: {r.message}")
        if not r.passed:
            fail += 1
    print(f"Report: {OUT_DIR / 'REPORT.md'}")
    if fail:
        print(f"FAILED: {fail}/{len(results)} scenarios regressed")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
