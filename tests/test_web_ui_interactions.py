"""Playwright-driven interaction tests for the Nellie web UI.

These complement ``tests/test_web_ui_visual.py`` (which only screenshots
each page at two viewports). Here we actually *click*, *type*, *submit*,
and assert DOM/URL/network-level consequences:

    1. New Session flow           — button click + 303 redirect to /sessions/<id>
    2. Send-message flow           — htmx POST, request interception, DOM update
    3. Recipes page empty state    — "No recipes" + install-hint paths
    4. Memory create modal         — modal open / form / modal close / row appears
    5. Navigation tabs             — nav links + active-tab styling

Each test takes a ``page.screenshot()`` before the critical assertion and
stores it under ``_web_interactions/<test_name>.png`` so CI failures are
visually debuggable.

Skips cleanly if Playwright + Chromium + the FastAPI web extras are
unavailable (common on dev laptops that haven't installed browsers).

Run with::

    pytest tests/test_web_ui_interactions.py -v
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

# Skip if Playwright isn't installable (common on Windows dev boxes).
pytest.importorskip("playwright")

try:  # The Python binding can install without the browser.
    from playwright.sync_api import Page, Route, sync_playwright
except ImportError:  # pragma: no cover
    pytest.skip("playwright python bindings missing", allow_module_level=True)

# Web UI optional extras.
pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

REPO_ROOT = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = REPO_ROOT / "_web_interactions"


# --------------------------------------------------------------------------- #
#  Server lifecycle (mirrors tools/web_ui_audit.py)
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


def _start_web_server(port: int) -> subprocess.Popen:
    import shutil

    nellie = shutil.which("nellie")
    if nellie:
        cmd = [nellie, "web", "--host", "127.0.0.1", "--port", str(port)]
    else:
        cmd = [
            sys.executable,
            "-m",
            "karna.cli",
            "web",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    return subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _stop_web_server(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _chromium_available() -> bool:
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Fixtures — one server + browser per module, fresh page per test
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def _chromium_or_skip() -> None:
    if not _chromium_available():
        pytest.skip("chromium not installed; run `python -m playwright install chromium`")


@pytest.fixture(scope="module")
def live_server(_chromium_or_skip) -> Iterator[str]:
    """Spawn ``nellie web`` on a free port and yield its base URL."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    port = _pick_free_port()
    base_url = f"http://127.0.0.1:{port}"
    proc = _start_web_server(port)
    try:
        if not _wait_for_health(f"{base_url}/api/health", timeout=45.0):
            if not _wait_for_health(f"{base_url}/health", timeout=5.0):
                _stop_web_server(proc)
                pytest.skip("web server never became healthy")
        yield base_url
    finally:
        _stop_web_server(proc)


@pytest.fixture(scope="module")
def browser(_chromium_or_skip):
    headless = os.environ.get("PLAYWRIGHT_HEADLESS", "1") == "1"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        yield browser
        browser.close()


@pytest.fixture
def page(browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1400, "height": 900})
    p = context.new_page()
    yield p
    context.close()


def _snap(page: Page, name: str) -> None:
    """Save a debug screenshot. Failures in this must never mask the test."""
    try:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SCREENSHOT_DIR / f"{name}.png"), full_page=False)
    except Exception:  # pragma: no cover - defensive
        pass


# --------------------------------------------------------------------------- #
#  1. New Session flow
# --------------------------------------------------------------------------- #


@pytest.mark.timeout(60)
def test_new_session_button_creates_and_navigates(live_server, page):
    """Clicking 'New Session' POSTs /sessions/new and lands on /sessions/<id>."""
    page.goto(f"{live_server}/", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle", timeout=10000)

    _snap(page, "new_session_before_click")

    # Semantic locator — survives styling/class renames.
    button = page.get_by_role("button", name="New Session")
    assert button.count() >= 1, "'New Session' button missing on /"

    # Click and follow the POST → 303 → GET redirect chain.
    with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
        button.first.click()

    _snap(page, "new_session_after_click")

    # URL should now be /sessions/<uuid-ish>.
    current = page.url
    assert "/sessions/" in current, f"expected /sessions/<id>, got {current}"
    sid = current.rsplit("/sessions/", 1)[-1].split("?")[0].strip("/")
    assert sid, f"no session id in URL {current}"
    # Sanity — session ids are uuid4 hex strings or similar.
    assert len(sid) >= 8, f"session id too short: {sid!r}"

    # The session detail page should have a message input.
    input_box = page.locator("#message-input, textarea[name='content']")
    assert input_box.count() >= 1, "session page missing message input"


# --------------------------------------------------------------------------- #
#  2. Send-message flow (stubbed — no real LLM)
# --------------------------------------------------------------------------- #


@pytest.mark.timeout(60)
def test_send_message_hits_backend_and_updates_dom(live_server, page):
    """Typing a message + submit hits /sessions/{id}/send and updates transcript.

    We stub the htmx POST with ``page.route()`` and return a minimal HTML
    partial (the shape of ``partials/messages.html``) so we don't have to
    spin up a real LLM. This also proves the form wiring without needing
    the SSE loop to emit anything.
    """
    # Bootstrap a session via the REST API (shortcut; covered by test #1).
    req = urllib.request.Request(
        f"{live_server}/api/v1/sessions",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        import json as _json

        sid = _json.loads(resp.read().decode("utf-8"))["id"]

    send_url_fragment = f"/sessions/{sid}/send"
    captured: dict[str, object] = {"hits": 0, "payload": None}

    def _intercept_send(route: Route) -> None:
        captured["hits"] = int(captured["hits"]) + 1  # type: ignore[operator]
        req = route.request
        captured["payload"] = req.post_data
        # Return a fake messages partial with both the user's echo and a
        # synthetic assistant reply. Same structure as
        # karna/web/templates/partials/messages.html.
        body = (
            '<div class="message message-user">'
            '<div class="message-role">user</div>'
            '<div class="message-content">hello from playwright</div>'
            "</div>"
            '<div class="message message-assistant">'
            '<div class="message-role">assistant</div>'
            '<div class="message-content">stubbed reply</div>'
            "</div>"
        )
        route.fulfill(status=200, content_type="text/html", body=body)

    page.route(f"**{send_url_fragment}", _intercept_send)

    page.goto(f"{live_server}/sessions/{sid}", wait_until="domcontentloaded")
    # Don't wait for networkidle: session page has a persistent
    # EventSource on /stream, so the network is never idle. Wait for the
    # concrete element we need instead.
    page.wait_for_selector("#message-input", timeout=10000)

    # Type the message, then click Send (don't rely on Enter — Shift+Enter
    # behaviour could vary).
    page.locator("#message-input").fill("hello from playwright")
    _snap(page, "send_message_before_submit")

    with page.expect_response(lambda r: send_url_fragment in r.url, timeout=10000) as resp_info:
        page.get_by_role("button", name="Send").click()
    resp = resp_info.value
    assert resp.status == 200, f"send endpoint returned {resp.status}"

    # Our route handler fired at least once and received the content.
    assert captured["hits"] >= 1, "expected /sessions/{id}/send to be POSTed"
    payload = captured["payload"] or ""
    assert "hello" in str(payload), f"POST body missing user content: {payload!r}"

    # htmx should have swapped the stubbed partial into #messages.
    page.wait_for_selector(".message-user", timeout=5000)
    _snap(page, "send_message_after_swap")
    transcript_text = page.locator("#transcript").inner_text()
    assert "hello from playwright" in transcript_text, transcript_text
    assert "stubbed reply" in transcript_text, transcript_text


# --------------------------------------------------------------------------- #
#  3. Recipes empty state
# --------------------------------------------------------------------------- #


@pytest.mark.timeout(30)
def test_recipes_empty_state_shows_install_hint(live_server, page):
    """/recipes with no recipes indexed renders the empty-state install hint."""
    page.goto(f"{live_server}/recipes", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle", timeout=10000)

    _snap(page, "recipes_empty_state")

    body_text = page.locator("body").inner_text()
    # The repo ships no recipes under ~/.karna or ./.karna in CI, so the
    # empty-state branch should render. If someone adds fixture recipes
    # later, this assertion will need to relax.
    if "No recipes found" not in body_text:
        pytest.skip("recipes indexed in this environment; empty-state branch not rendered")

    # Install-hint paths. The template escapes tildes, so substring match
    # covers both raw and HTML-rendered output.
    hint_hit = any(token in body_text for token in ("~/.karna/recipes/", ".karna/recipes/"))
    assert hint_hit, f"empty-state install hint missing in body: {body_text!r}"


# --------------------------------------------------------------------------- #
#  4. Memory create modal
# --------------------------------------------------------------------------- #


@pytest.mark.timeout(60)
def test_memory_create_modal_opens_submits_and_row_appears(live_server, page):
    """Open the create modal, submit a new memory, assert it shows in list."""
    page.goto(f"{live_server}/memory", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle", timeout=10000)

    # Click 'New Memory' — template uses a <button class="btn btn-primary">
    # with text "New Memory" that adds .active to #create-modal.
    open_button = page.get_by_role("button", name="New Memory")
    assert open_button.count() >= 1, "'New Memory' button missing on /memory"
    open_button.first.click()

    # Modal becomes visible via .modal.active { display: flex }.
    modal = page.locator("#create-modal")
    modal.wait_for(state="visible", timeout=5000)
    assert "active" in (modal.get_attribute("class") or ""), "create-modal did not get .active class"

    # Unique name so we can assert the row shows up after redirect.
    unique_name = f"pw-test-{uuid.uuid4().hex[:8]}"
    page.locator("#name").fill(unique_name)
    page.locator("#description").fill("playwright interaction test memory")
    page.locator("#content").fill("this memory was created by test_web_ui_interactions")

    _snap(page, "memory_modal_before_submit")

    # Submit the form; it redirects (303) back to /memory.
    with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
        page.locator("#create-modal form button[type='submit']").click()

    page.wait_for_load_state("networkidle", timeout=10000)
    _snap(page, "memory_after_create")

    # Modal should be gone (page re-rendered from scratch, no .active).
    # The row should appear somewhere on the page.
    body_text = page.locator("body").inner_text()
    assert unique_name in body_text, f"newly created memory {unique_name!r} not visible on /memory"


# --------------------------------------------------------------------------- #
#  5. Navigation tabs + active styling
# --------------------------------------------------------------------------- #


@pytest.mark.timeout(45)
def test_nav_tabs_navigate_and_apply_active_class(live_server, page):
    """Clicking each nav link routes correctly and marks itself active."""
    page.goto(f"{live_server}/", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle", timeout=10000)

    # On /, the Sessions nav-link has the .active class per base.html
    # ({% block nav_sessions %}active{% endblock %} resolved on index.html).
    sessions_link = page.get_by_role("link", name="Sessions")
    recipes_link = page.get_by_role("link", name="Recipes")
    memory_link = page.get_by_role("link", name="Memory")

    assert sessions_link.count() >= 1, "Sessions nav link missing"
    assert recipes_link.count() >= 1, "Recipes nav link missing"
    assert memory_link.count() >= 1, "Memory nav link missing"

    # Helper: the active nav-link's class string includes 'active'.
    def _assert_active(label: str) -> None:
        loc = page.get_by_role("link", name=label).first
        cls = loc.get_attribute("class") or ""
        assert "nav-link" in cls, f"{label} not a nav-link? class={cls!r}"
        assert "active" in cls, f"expected {label} nav-link to have 'active' class, got {cls!r}"

    _snap(page, "nav_on_index")
    _assert_active("Sessions")

    # Click Recipes → /recipes
    with page.expect_navigation(wait_until="domcontentloaded", timeout=10000):
        recipes_link.first.click()
    assert page.url.rstrip("/").endswith("/recipes"), page.url
    _snap(page, "nav_on_recipes")
    _assert_active("Recipes")

    # Click Memory → /memory
    with page.expect_navigation(wait_until="domcontentloaded", timeout=10000):
        page.get_by_role("link", name="Memory").first.click()
    assert page.url.rstrip("/").endswith("/memory"), page.url
    _snap(page, "nav_on_memory")
    _assert_active("Memory")

    # Click Sessions → /
    with page.expect_navigation(wait_until="domcontentloaded", timeout=10000):
        page.get_by_role("link", name="Sessions").first.click()
    assert page.url.rstrip("/") == live_server.rstrip("/"), page.url
    _snap(page, "nav_back_to_index")
    _assert_active("Sessions")
