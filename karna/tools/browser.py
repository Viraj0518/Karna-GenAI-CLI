"""Browser interaction tool — browse pages, fill forms, take screenshots.

Wraps the ``playwright`` Python package to provide headless Chromium
automation.  Playwright is an **optional** dependency — if it is not
installed the tool reports a clear installation hint and never crashes.

The browser is lazily launched on first use and kept alive across calls
so the agent can navigate, click, fill forms, and capture screenshots
within a single session.

PRIVACY: This tool drives a local headless browser.  Page loads go to
the requested URLs.  No telemetry, no analytics, no data sent to Karna
developers.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from karna.security import is_safe_url
from karna.tools.base import BaseTool

log = logging.getLogger(__name__)

_PLAYWRIGHT_HINT = (
    "[error] playwright is not installed.  "
    "Install it with:  pip install 'karna[browser]'  "
    "then run:  playwright install chromium"
)


class BrowserTool(BaseTool):
    """Browse web pages, interact with forms, take screenshots.

    Requires the optional ``playwright`` dependency.  A headless
    Chromium instance is lazily launched on the first call and reused
    until ``action=close`` or object cleanup.
    """

    name = "browser"
    description = "Browse web pages, interact with forms, take screenshots"
    sequential = True  # browser state is inherently sequential
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["navigate", "click", "fill", "screenshot", "content", "close"],
                "description": "Browser action to perform",
            },
            "url": {
                "type": "string",
                "description": "URL to navigate to (for action=navigate)",
            },
            "selector": {
                "type": "string",
                "description": "CSS selector for the target element (for action=click/fill)",
            },
            "text": {
                "type": "string",
                "description": "Text to type into the element (for action=fill)",
            },
        },
        "required": ["action"],
    }

    def __init__(self) -> None:
        super().__init__()
        # These are set lazily on first use.
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None

    # ------------------------------------------------------------------ #
    #  Lifecycle helpers
    # ------------------------------------------------------------------ #

    async def _ensure_browser(self) -> None:
        """Launch playwright + headless Chromium if not already running."""
        if self._page is not None:
            return

        try:
            from playwright.async_api import async_playwright  # type: ignore[import-untyped]
        except ImportError:
            raise _PlaywrightMissing from None

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._page = await self._browser.new_page()

        # Per-request SSRF guard. Runs for every network request the page
        # makes, including redirect targets and subresources, so a safe
        # initial URL that 302s to 169.254.169.254 or a host that flips
        # DNS between the initial validation and the actual connection
        # will be aborted at the browser-network boundary.
        #
        # Playwright's Python binding always invokes the handler as
        # ``handler(route, route.request)`` (see playwright/_impl/_helper.py),
        # so we take both positional args even though we only need `route`.
        async def _ssrf_route(route: Any, request: Any) -> None:  # pragma: no cover - thin
            req_url = request.url
            if not is_safe_url(req_url):
                log.warning("Blocked in-browser request to unsafe URL: %s", req_url)
                await route.abort("accessdenied")
                return
            await route.continue_()

        await self._page.route("**/*", _ssrf_route)
        log.debug("Headless Chromium launched")

    async def _close(self) -> str:
        """Shut down the browser and playwright."""
        if self._page is not None:
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        return "Browser closed."

    # ------------------------------------------------------------------ #
    #  Core interface
    # ------------------------------------------------------------------ #

    async def execute(self, **kwargs: Any) -> str:
        action: str = kwargs.get("action", "")
        if not action:
            return "[error] Missing required parameter: action"

        # Close does not need a running browser.
        if action == "close":
            return await self._close()

        # Every other action needs the browser.
        try:
            await self._ensure_browser()
        except _PlaywrightMissing:
            return _PLAYWRIGHT_HINT

        try:
            if action == "navigate":
                return await self._do_navigate(kwargs)
            if action == "click":
                return await self._do_click(kwargs)
            if action == "fill":
                return await self._do_fill(kwargs)
            if action == "screenshot":
                return await self._do_screenshot()
            if action == "content":
                return await self._do_content()
            return (
                f"[error] Unknown action: {action!r}. "
                "Must be one of: navigate, click, fill, screenshot, content, close."
            )
        except Exception as exc:
            return f"[error] {type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------ #
    #  Actions
    # ------------------------------------------------------------------ #

    async def _do_navigate(self, kwargs: dict[str, Any]) -> str:
        url: str | None = kwargs.get("url")
        if not url or not url.strip():
            return "[error] action=navigate requires a url parameter."
        if not is_safe_url(url):
            return (
                "[error] Blocked: URL targets a private/internal network address. "
                "localhost, RFC-1918, link-local, and cloud metadata endpoints are not allowed."
            )
        await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        title = await self._page.title()
        return f"Navigated to {url}\nTitle: {title}"

    async def _do_click(self, kwargs: dict[str, Any]) -> str:
        selector: str | None = kwargs.get("selector")
        if not selector or not selector.strip():
            return "[error] action=click requires a selector parameter."
        await self._page.click(selector, timeout=10_000)
        return f"Clicked: {selector}"

    async def _do_fill(self, kwargs: dict[str, Any]) -> str:
        selector: str | None = kwargs.get("selector")
        text: str | None = kwargs.get("text")
        if not selector or not selector.strip():
            return "[error] action=fill requires a selector parameter."
        if text is None:
            return "[error] action=fill requires a text parameter."
        await self._page.fill(selector, text, timeout=10_000)
        return f"Filled {selector} with text ({len(text)} chars)"

    async def _do_screenshot(self) -> str:
        raw: bytes = await self._page.screenshot(type="png", full_page=False)
        b64 = base64.b64encode(raw).decode()
        return f"Screenshot captured ({len(raw)} bytes)\ndata:image/png;base64,{b64}"

    async def _do_content(self) -> str:
        content: str = await self._page.content()
        # Strip to a reasonable length for the agent context.
        max_len = 20_000
        if len(content) > max_len:
            content = content[:max_len] + "\n\n[... truncated]"
        return content

    # ------------------------------------------------------------------ #
    #  Cleanup
    # ------------------------------------------------------------------ #

    def __del__(self) -> None:
        """Best-effort synchronous cleanup.

        The proper way to close is ``action=close``.  This fallback
        avoids resource leaks if the caller forgets.
        """
        if self._browser is not None or self._playwright is not None:
            log.debug("BrowserTool.__del__: browser was not explicitly closed")
            # Cannot await in __del__; resources will be freed when the
            # process exits.  Set references to None so GC can collect.
            self._page = None
            self._browser = None
            self._playwright = None


class _PlaywrightMissing(Exception):
    """Raised internally when playwright is not importable."""
