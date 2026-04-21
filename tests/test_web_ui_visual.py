"""Playwright-driven visual regression for the Nellie web UI.

Skips cleanly unless Playwright (and its Chromium browser binary) are
installed. Actual audit logic lives in ``tools/web_ui_audit.py``.

Run with::

    pytest tests/test_web_ui_visual.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Skip if Playwright isn't installable (common on Windows dev boxes).
pytest.importorskip("playwright")

try:  # The Python binding can install without the browser.
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    pytest.skip("playwright python bindings missing", allow_module_level=True)

# Web UI optional extras.
pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

from web_ui_audit import run_audit  # noqa: E402


def _chromium_available() -> bool:
    """Return True iff playwright can actually launch Chromium."""
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


@pytest.mark.timeout(180)
def test_web_ui_visual_audit_all_pages_pass():
    """End-to-end audit: every mapped page at desktop + mobile must pass."""
    if not _chromium_available():
        pytest.skip("chromium not installed; run `python -m playwright install chromium`")

    results, report = run_audit(headless=True)
    assert report.exists(), "REPORT.md was not written"
    assert results, "no pages were audited"

    failed = [r for r in results if not r.ok]
    if failed:
        # Surface the first few failures with detail so CI logs are useful.
        msgs = [f"{r.slug}@{r.viewport}: status={r.status} err={r.error}" for r in failed]
        raise AssertionError(
            f"{len(failed)}/{len(results)} page audits failed:\n  - "
            + "\n  - ".join(msgs)
        )


@pytest.mark.timeout(180)
def test_web_ui_visual_audit_no_console_errors():
    """Console errors are a blocker. Fail even if status/allowlist were fine."""
    if not _chromium_available():
        pytest.skip("chromium not installed; run `python -m playwright install chromium`")

    results, _ = run_audit(headless=True)
    noisy = [r for r in results if r.console_errors]
    assert not noisy, (
        f"{len(noisy)} page(s) produced JS console errors: "
        + "; ".join(f"{r.slug}@{r.viewport}:{r.console_errors[:2]}" for r in noisy)
    )
