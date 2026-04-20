"""Tests for the browser interaction tool.

Covers all six actions (navigate, click, fill, screenshot, content,
close), graceful degradation when playwright is not installed,
parameter validation, and registry integration.

Playwright is mocked throughout — no real browser is launched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from karna.tools import get_all_tools, get_tool
from karna.tools.browser import BrowserTool

# ======================================================================= #
#  Helpers / Fixtures
# ======================================================================= #


@pytest.fixture()
def tool() -> BrowserTool:
    """Return a fresh BrowserTool instance."""
    return BrowserTool()


def _make_mock_page() -> AsyncMock:
    """Build a mock playwright Page with sensible defaults."""
    page = AsyncMock()
    page.title.return_value = "Mock Page"
    page.goto.return_value = None
    page.click.return_value = None
    page.fill.return_value = None
    page.screenshot.return_value = b"\x89PNG\r\n\x1a\nfakedata"
    page.content.return_value = "<html><body>Hello</body></html>"
    page.close.return_value = None
    return page


def _make_mock_browser(page: AsyncMock) -> AsyncMock:
    """Build a mock playwright Browser."""
    browser = AsyncMock()
    browser.new_page.return_value = page
    browser.close.return_value = None
    return browser


def _make_mock_playwright(browser: AsyncMock) -> AsyncMock:
    """Build a mock playwright context manager."""
    pw = AsyncMock()
    pw.chromium.launch.return_value = browser
    pw.stop.return_value = None
    return pw


async def _inject_mocks(tool: BrowserTool) -> AsyncMock:
    """Inject mocked playwright objects into *tool* and return the page mock."""
    page = _make_mock_page()
    browser = _make_mock_browser(page)
    pw = _make_mock_playwright(browser)
    tool._playwright = pw
    tool._browser = browser
    tool._page = page
    return page


# ======================================================================= #
#  Navigate
# ======================================================================= #


class TestNavigate:
    @pytest.mark.asyncio
    async def test_navigate_success(self, tool: BrowserTool):
        page = await _inject_mocks(tool)
        result = await tool.execute(action="navigate", url="https://example.com")
        page.goto.assert_awaited_once()
        assert "example.com" in result
        assert "Mock Page" in result

    @pytest.mark.asyncio
    async def test_navigate_missing_url(self, tool: BrowserTool):
        await _inject_mocks(tool)
        result = await tool.execute(action="navigate")
        assert "[error]" in result
        assert "url" in result.lower()

    @pytest.mark.asyncio
    async def test_navigate_empty_url(self, tool: BrowserTool):
        await _inject_mocks(tool)
        result = await tool.execute(action="navigate", url="")
        assert "[error]" in result

    @pytest.mark.asyncio
    async def test_navigate_whitespace_url(self, tool: BrowserTool):
        await _inject_mocks(tool)
        result = await tool.execute(action="navigate", url="   ")
        assert "[error]" in result

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8080/admin",
            "http://127.0.0.1/secret",
            "http://10.0.0.1/internal",
            "http://192.168.1.1/router",
            "http://169.254.169.254/latest/meta-data/",
            "ftp://example.com/file",
        ],
    )
    async def test_navigate_ssrf_blocked(self, tool: BrowserTool, url: str):
        """Private/internal URLs and non-HTTP schemes must be rejected."""
        page = await _inject_mocks(tool)
        result = await tool.execute(action="navigate", url=url)
        assert "[error]" in result
        assert "blocked" in result.lower()
        page.goto.assert_not_awaited()


# ======================================================================= #
#  Click
# ======================================================================= #


class TestClick:
    @pytest.mark.asyncio
    async def test_click_success(self, tool: BrowserTool):
        page = await _inject_mocks(tool)
        result = await tool.execute(action="click", selector="#submit-btn")
        page.click.assert_awaited_once_with("#submit-btn", timeout=10_000)
        assert "Clicked" in result

    @pytest.mark.asyncio
    async def test_click_missing_selector(self, tool: BrowserTool):
        await _inject_mocks(tool)
        result = await tool.execute(action="click")
        assert "[error]" in result
        assert "selector" in result.lower()

    @pytest.mark.asyncio
    async def test_click_empty_selector(self, tool: BrowserTool):
        await _inject_mocks(tool)
        result = await tool.execute(action="click", selector="")
        assert "[error]" in result


# ======================================================================= #
#  Fill
# ======================================================================= #


class TestFill:
    @pytest.mark.asyncio
    async def test_fill_success(self, tool: BrowserTool):
        page = await _inject_mocks(tool)
        result = await tool.execute(action="fill", selector="input[name=q]", text="hello")
        page.fill.assert_awaited_once_with("input[name=q]", "hello", timeout=10_000)
        assert "Filled" in result
        assert "5 chars" in result

    @pytest.mark.asyncio
    async def test_fill_missing_selector(self, tool: BrowserTool):
        await _inject_mocks(tool)
        result = await tool.execute(action="fill", text="hello")
        assert "[error]" in result
        assert "selector" in result.lower()

    @pytest.mark.asyncio
    async def test_fill_missing_text(self, tool: BrowserTool):
        await _inject_mocks(tool)
        result = await tool.execute(action="fill", selector="input")
        assert "[error]" in result
        assert "text" in result.lower()

    @pytest.mark.asyncio
    async def test_fill_empty_text_allowed(self, tool: BrowserTool):
        """Filling with an empty string is valid (e.g. clearing a field)."""
        page = await _inject_mocks(tool)
        result = await tool.execute(action="fill", selector="input", text="")
        page.fill.assert_awaited_once()
        assert "Filled" in result


# ======================================================================= #
#  Screenshot
# ======================================================================= #


class TestScreenshot:
    @pytest.mark.asyncio
    async def test_screenshot_returns_base64(self, tool: BrowserTool):
        page = await _inject_mocks(tool)
        result = await tool.execute(action="screenshot")
        page.screenshot.assert_awaited_once()
        assert "data:image/png;base64," in result
        assert "Screenshot captured" in result


# ======================================================================= #
#  Content
# ======================================================================= #


class TestContent:
    @pytest.mark.asyncio
    async def test_content_returns_html(self, tool: BrowserTool):
        page = await _inject_mocks(tool)
        result = await tool.execute(action="content")
        page.content.assert_awaited_once()
        assert "<html>" in result
        assert "Hello" in result

    @pytest.mark.asyncio
    async def test_content_truncation(self, tool: BrowserTool):
        page = await _inject_mocks(tool)
        page.content.return_value = "A" * 30_000
        result = await tool.execute(action="content")
        assert "[... truncated]" in result
        assert len(result) < 25_000


# ======================================================================= #
#  Close
# ======================================================================= #


class TestClose:
    @pytest.mark.asyncio
    async def test_close_running_browser(self, tool: BrowserTool):
        await _inject_mocks(tool)
        result = await tool.execute(action="close")
        assert "closed" in result.lower()
        assert tool._page is None
        assert tool._browser is None
        assert tool._playwright is None

    @pytest.mark.asyncio
    async def test_close_when_not_running(self, tool: BrowserTool):
        """Closing an already-closed browser should not error."""
        result = await tool.execute(action="close")
        assert "closed" in result.lower()


# ======================================================================= #
#  Validation
# ======================================================================= #


class TestValidation:
    @pytest.mark.asyncio
    async def test_missing_action(self, tool: BrowserTool):
        await _inject_mocks(tool)
        result = await tool.execute()
        assert "[error]" in result

    @pytest.mark.asyncio
    async def test_unknown_action(self, tool: BrowserTool):
        await _inject_mocks(tool)
        result = await tool.execute(action="explode")
        assert "[error]" in result
        assert "Unknown action" in result

    @pytest.mark.asyncio
    async def test_playwright_error_propagation(self, tool: BrowserTool):
        """Errors from playwright calls should be caught and returned."""
        page = await _inject_mocks(tool)
        page.goto.side_effect = Exception("net::ERR_NAME_NOT_RESOLVED")
        result = await tool.execute(action="navigate", url="https://does.not.exist.example")
        assert "[error]" in result
        assert "ERR_NAME_NOT_RESOLVED" in result


# ======================================================================= #
#  Graceful degradation (playwright not installed)
# ======================================================================= #


class TestPlaywrightMissing:
    @pytest.mark.asyncio
    async def test_navigate_without_playwright(self, tool: BrowserTool):
        with patch.dict("sys.modules", {"playwright": None, "playwright.async_api": None}):
            result = await tool.execute(action="navigate", url="https://example.com")
            assert "[error]" in result
            assert "playwright" in result.lower()
            assert "pip install" in result

    @pytest.mark.asyncio
    async def test_screenshot_without_playwright(self, tool: BrowserTool):
        with patch.dict("sys.modules", {"playwright": None, "playwright.async_api": None}):
            result = await tool.execute(action="screenshot")
            assert "[error]" in result
            assert "playwright" in result.lower()

    @pytest.mark.asyncio
    async def test_close_without_playwright_does_not_error(self, tool: BrowserTool):
        """Close should work even if playwright was never loaded."""
        result = await tool.execute(action="close")
        assert "[error]" not in result


# ======================================================================= #
#  Lazy launch
# ======================================================================= #


class TestLazyLaunch:
    @pytest.mark.asyncio
    async def test_browser_launched_on_first_action(self):
        """_ensure_browser should be called for non-close actions."""
        tool = BrowserTool()
        mock_ensure = AsyncMock()
        tool._ensure_browser = mock_ensure  # type: ignore[method-assign]
        # Inject a page so subsequent action code doesn't blow up.
        tool._page = _make_mock_page()
        await tool.execute(action="content")
        mock_ensure.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_browser_not_launched_on_close(self):
        """Close should NOT trigger _ensure_browser."""
        tool = BrowserTool()
        mock_ensure = AsyncMock()
        tool._ensure_browser = mock_ensure  # type: ignore[method-assign]
        await tool.execute(action="close")
        mock_ensure.assert_not_awaited()


# ======================================================================= #
#  Registry integration
# ======================================================================= #


class TestBrowserToolRegistry:
    def test_get_browser_tool(self):
        tool = get_tool("browser")
        assert tool.name == "browser"

    def test_all_tools_includes_browser(self):
        tools = get_all_tools()
        names = {t.name for t in tools}
        assert "browser" in names

    def test_tool_properties(self):
        tool = BrowserTool()
        assert tool.name == "browser"
        assert tool.sequential is True
        assert "action" in tool.parameters["properties"]

    def test_anthropic_tool_format(self):
        tool = BrowserTool()
        fmt = tool.to_anthropic_tool()
        assert fmt["name"] == "browser"
        assert "input_schema" in fmt

    def test_openai_tool_format(self):
        tool = BrowserTool()
        fmt = tool.to_openai_tool()
        assert fmt["type"] == "function"
        assert fmt["function"]["name"] == "browser"
