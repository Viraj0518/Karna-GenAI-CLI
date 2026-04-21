"""Pytest wrapper around ``tools/visual_regression.py``.

One test per scenario. Skips when no rendering backend is available
(cairosvg lib missing + no playwright browsers installed) AND no
committed ANSI baseline exists — i.e. nothing useful to compare.

The actual rendering + diff logic lives in ``tools/visual_regression.py``.
This file keeps the CI surface small and legible.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ansi_to_png import detect_backend  # noqa: E402
from tools.visual_regression import (  # noqa: E402
    BASELINE_DIR,
    SCENARIOS,
    run_check,
)

# 2% pixel tolerance — matches CLI default. Tight enough to catch real
# regressions, loose enough to survive sub-pixel antialiasing drift
# between runners.
THRESHOLD = 0.02


@pytest.fixture(scope="module")
def _check_results() -> dict[str, object]:
    backend = detect_backend()
    # If fallback backend is in play AND we have no .ansi baselines, nothing
    # to test — skip the whole module.
    if backend == "ansi-text" and not any((BASELINE_DIR / f"{n}.ansi").exists() for n in SCENARIOS):
        pytest.skip("no renderable backend and no ANSI baselines committed")
    # If pixel backend is active but no .png baselines exist, also skip.
    if backend != "ansi-text" and not any((BASELINE_DIR / f"{n}.png").exists() and (BASELINE_DIR / f"{n}.png").stat().st_size > 0 for n in SCENARIOS):
        pytest.skip(f"{backend} backend detected but no committed PNG baselines")

    results, used_backend = run_check(THRESHOLD, backend_hint=None)
    return {r.name: r for r in results} | {"__backend__": used_backend}


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_scenario_within_threshold(_check_results: dict, scenario: str) -> None:  # type: ignore[type-arg]
    result = _check_results[scenario]  # type: ignore[assignment]
    assert result.passed, (  # type: ignore[attr-defined]
        f"[{scenario}] visual regression exceeded {THRESHOLD*100:.2f}%: "
        f"{result.message}  (see _visual_diff/REPORT.md)"  # type: ignore[attr-defined]
    )
