"""Playwright visual/functional audit for the Nellie web UI.

Spawns ``nellie web`` as a subprocess, polls ``/health`` until ready, then
drives headed (or headless if ``PLAYWRIGHT_HEADLESS=1``) Chromium through
each major page at desktop + mobile viewports.

For every page the script:
  * screenshots the viewport to ``_web_screenshots/<page>_<viewport>.png``
  * asserts HTTP 200, ``<title>`` contains "Nellie" or "Karna"
  * asserts at least one visible element matches the per-page allowlist
  * captures JS console errors via ``page.on("console")``

Produces ``_web_screenshots/REPORT.md`` with embedded images and a pass /
fail line per (page, viewport).

Run directly::

    python tools/web_ui_audit.py

Exit code is non-zero if any page fails.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = REPO_ROOT / "_web_screenshots"

# --------------------------------------------------------------------------- #
#  Page map
# --------------------------------------------------------------------------- #


@dataclass
class PageSpec:
    """One page to audit."""

    slug: str
    path: str
    # At least one of these substrings must appear as visible text.
    allowlist: list[str]
    # If True, the route is created on the fly via POST /api/v1/sessions
    # and the path is formatted with ``{sid}``.
    needs_session: bool = False


PAGES: list[PageSpec] = [
    PageSpec(slug="index", path="/", allowlist=["Sessions", "Nellie", "New Session"]),
    PageSpec(slug="sessions", path="/", allowlist=["Sessions"]),  # same as index
    PageSpec(slug="session_detail", path="/sessions/{sid}", allowlist=["Session", "Send"], needs_session=True),
    PageSpec(slug="recipes", path="/recipes", allowlist=["Recipe"]),
    PageSpec(slug="memory", path="/memory", allowlist=["Memory"]),
]

VIEWPORTS: list[tuple[str, int, int]] = [
    ("desktop", 1400, 900),
    ("mobile", 390, 844),
]


# --------------------------------------------------------------------------- #
#  Result types
# --------------------------------------------------------------------------- #


@dataclass
class PageResult:
    slug: str
    path: str
    viewport: str
    status: int = 0
    title: str = ""
    screenshot: str = ""
    allowlist_hit: str = ""
    console_errors: list[str] = field(default_factory=list)
    ok: bool = False
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "path": self.path,
            "viewport": self.viewport,
            "status": self.status,
            "title": self.title,
            "screenshot": self.screenshot,
            "allowlist_hit": self.allowlist_hit,
            "console_errors": self.console_errors,
            "ok": self.ok,
            "error": self.error,
        }


# --------------------------------------------------------------------------- #
#  Server lifecycle helpers
# --------------------------------------------------------------------------- #


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            pass
        time.sleep(0.25)
    return False


def start_web_server(port: int) -> subprocess.Popen:
    """Launch ``nellie web`` (or fall back to ``python -m karna.cli web``)."""
    nellie = shutil.which("nellie")
    if nellie:
        cmd = [nellie, "web", "--host", "127.0.0.1", "--port", str(port)]
    else:
        cmd = [sys.executable, "-m", "karna.cli", "web", "--host", "127.0.0.1", "--port", str(port)]
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc


def stop_web_server(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


# --------------------------------------------------------------------------- #
#  Session bootstrap (for /sessions/{id})
# --------------------------------------------------------------------------- #


def _create_session(base_url: str) -> str | None:
    """POST /api/v1/sessions and return the new session id."""
    try:
        req = urllib.request.Request(
            f"{base_url}/api/v1/sessions",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            if resp.status == 200:
                payload = json.loads(resp.read().decode("utf-8"))
                return payload.get("id")
    except Exception:
        return None
    return None


# --------------------------------------------------------------------------- #
#  Core audit
# --------------------------------------------------------------------------- #


def _audit_page(
    context: Any,
    base_url: str,
    spec: PageSpec,
    vp_name: str,
    screenshot_dir: Path,
    session_id: str | None,
) -> PageResult:
    path = spec.path.format(sid=session_id) if spec.needs_session else spec.path
    result = PageResult(slug=spec.slug, path=path, viewport=vp_name)

    page = context.new_page()
    console_errors: list[str] = []

    def _on_console(msg: Any) -> None:
        try:
            if msg.type == "error":
                console_errors.append(msg.text)
        except Exception:  # pragma: no cover - defensive
            pass

    page.on("console", _on_console)
    page.on("pageerror", lambda err: console_errors.append(f"pageerror: {err}"))

    try:
        response = page.goto(f"{base_url}{path}", wait_until="domcontentloaded", timeout=15000)
        result.status = response.status if response else 0
        # Don't wait for ``networkidle`` — session detail pages hold a
        # long-lived EventSource on /stream and never go idle. "load" is
        # enough: the DOM is parsed, fonts + stylesheets are resolved,
        # scripts have run. That's all we need to screenshot + inspect.
        page.wait_for_load_state("load", timeout=10000)

        result.title = page.title() or ""

        # Screenshot
        shot = screenshot_dir / f"{spec.slug}_{vp_name}.png"
        page.screenshot(path=str(shot), full_page=False)
        result.screenshot = shot.name

        # Assertions
        if result.status != 200:
            result.error = f"HTTP {result.status}"
        elif not any(tok in result.title for tok in ("Nellie", "Karna")):
            result.error = f"title missing Nellie/Karna: {result.title!r}"
        else:
            body_text = page.locator("body").inner_text()
            hit = next((t for t in spec.allowlist if t.lower() in body_text.lower()), "")
            if not hit:
                result.error = f"no allowlist hit in body; looked for {spec.allowlist}"
            else:
                result.allowlist_hit = hit
                result.ok = True

        result.console_errors = list(console_errors)
        # Console errors demote the pass (hard fail)
        if result.ok and result.console_errors:
            result.ok = False
            result.error = f"console errors: {result.console_errors[:3]}"
    except Exception as exc:
        result.error = f"exception: {exc}"
        result.ok = False
    finally:
        page.close()

    return result


def run_audit(
    port: int | None = None,
    headless: bool | None = None,
) -> tuple[list[PageResult], Path]:
    """Full audit: start server, sweep pages, write report. Return (results, report_path)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("playwright not installed. pip install 'karna[web-test]'") from exc

    if headless is None:
        headless = os.environ.get("PLAYWRIGHT_HEADLESS", "1") == "1"

    if SCREENSHOT_DIR.exists():
        for p in SCREENSHOT_DIR.glob("*.png"):
            p.unlink()
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    if port is None:
        port = _pick_free_port()
    base_url = f"http://127.0.0.1:{port}"

    proc = start_web_server(port)
    results: list[PageResult] = []
    try:
        if not _wait_for_health(f"{base_url}/api/health", timeout=45.0):
            # Some builds of the REST app expose /health on both the
            # web root and /api. Try both before giving up.
            if not _wait_for_health(f"{base_url}/health", timeout=5.0):
                raise RuntimeError("web server never became healthy")

        session_id = _create_session(base_url)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            for vp_name, w, h in VIEWPORTS:
                context = browser.new_context(viewport={"width": w, "height": h})
                for spec in PAGES:
                    if spec.needs_session and not session_id:
                        results.append(
                            PageResult(
                                slug=spec.slug,
                                path=spec.path,
                                viewport=vp_name,
                                ok=False,
                                error="could not create session for detail page",
                            )
                        )
                        continue
                    results.append(_audit_page(context, base_url, spec, vp_name, SCREENSHOT_DIR, session_id))
                context.close()
            browser.close()
    finally:
        stop_web_server(proc)

    report_path = _write_report(results)
    return results, report_path


# --------------------------------------------------------------------------- #
#  Report writer
# --------------------------------------------------------------------------- #


def _write_report(results: list[PageResult]) -> Path:
    pass_count = sum(1 for r in results if r.ok)
    fail_count = len(results) - pass_count

    lines: list[str] = []
    lines.append("# Nellie Web UI Visual Audit")
    lines.append("")
    lines.append(f"- Total: **{len(results)}**  ")
    lines.append(f"- Passed: **{pass_count}**  ")
    lines.append(f"- Failed: **{fail_count}**")
    lines.append("")
    lines.append("| Page | Viewport | Status | Title | Allowlist hit | Console errs | OK |")
    lines.append("|------|----------|--------|-------|---------------|--------------|----|")
    for r in results:
        ok = "pass" if r.ok else "FAIL"
        lines.append(
            f"| `{r.slug}` | {r.viewport} | {r.status} | {r.title[:40]} | "
            f"{r.allowlist_hit or '-'} | {len(r.console_errors)} | {ok} |"
        )
    lines.append("")

    for r in results:
        lines.append(f"## `{r.slug}` — {r.viewport}")
        lines.append("")
        lines.append(f"- **path**: `{r.path}`")
        lines.append(f"- **status**: {r.status}")
        lines.append(f"- **title**: `{r.title}`")
        lines.append(f"- **ok**: {r.ok}")
        if r.error:
            lines.append(f"- **error**: `{r.error}`")
        if r.console_errors:
            lines.append("- **console errors**:")
            for e in r.console_errors:
                lines.append(f"  - `{e}`")
        if r.screenshot:
            lines.append("")
            lines.append(f"![{r.slug} {r.viewport}]({r.screenshot})")
        lines.append("")

    report = SCREENSHOT_DIR / "REPORT.md"
    report.write_text("\n".join(lines), encoding="utf-8")

    # Also drop a raw JSON dump for machine consumption.
    (SCREENSHOT_DIR / "results.json").write_text(
        json.dumps([r.as_dict() for r in results], indent=2),
        encoding="utf-8",
    )
    return report


# --------------------------------------------------------------------------- #
#  CLI entrypoint
# --------------------------------------------------------------------------- #


def main() -> int:
    try:
        results, report = run_audit()
    except Exception as exc:
        print(f"[web_ui_audit] FATAL: {exc}", file=sys.stderr)
        return 2

    print(f"[web_ui_audit] report written to {report}")
    failures = [r for r in results if not r.ok]
    if failures:
        print(f"[web_ui_audit] {len(failures)} page(s) failed:")
        for r in failures:
            print(f"  - {r.slug} @ {r.viewport}: {r.error}")
        return 1
    print(f"[web_ui_audit] all {len(results)} page(s) passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
