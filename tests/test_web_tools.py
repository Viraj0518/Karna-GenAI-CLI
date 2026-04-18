"""Tests for web_search and web_fetch tools.

Covers SSRF guard, robots.txt parser, basic extraction, and
(optionally) live network tests.
"""

from __future__ import annotations

import pytest

from karna.tools import get_all_tools, get_tool
from karna.tools.web_fetch import (
    WebFetchTool,
    _extract_text_basic,
    _is_path_allowed,
    _truncate_at_boundary,
    is_safe_url,
)
from karna.tools.web_search import WebSearchTool

# ======================================================================= #
#  SSRF Guard
# ======================================================================= #


class TestSSRFGuard:
    """is_safe_url must reject private/internal network addresses."""

    def test_rejects_loopback_127(self):
        assert is_safe_url("http://127.0.0.1") is False

    def test_rejects_loopback_localhost(self):
        assert is_safe_url("http://localhost") is False

    def test_rejects_private_10(self):
        assert is_safe_url("http://10.0.0.1") is False

    def test_rejects_private_172(self):
        assert is_safe_url("http://172.16.0.1") is False

    def test_rejects_private_192(self):
        assert is_safe_url("http://192.168.1.1") is False

    def test_rejects_ipv6_loopback(self):
        assert is_safe_url("http://[::1]") is False

    def test_rejects_ftp_scheme(self):
        assert is_safe_url("ftp://example.com/file.txt") is False

    def test_rejects_file_scheme(self):
        assert is_safe_url("file:///etc/passwd") is False

    def test_rejects_empty_url(self):
        assert is_safe_url("") is False

    def test_allows_public_url(self):
        assert is_safe_url("https://example.com") is True

    def test_allows_public_http(self):
        assert is_safe_url("http://example.com") is True


# ======================================================================= #
#  robots.txt Parser
# ======================================================================= #


class TestRobotsTxtParser:
    def test_empty_robots_allows_all(self):
        assert _is_path_allowed("", "/any/path") is True

    def test_disallow_all(self):
        robots = "User-agent: *\nDisallow: /"
        assert _is_path_allowed(robots, "/any/path") is False

    def test_disallow_specific_path(self):
        robots = "User-agent: *\nDisallow: /private/"
        assert _is_path_allowed(robots, "/private/secret") is False
        assert _is_path_allowed(robots, "/public/page") is True

    def test_allow_overrides_disallow(self):
        robots = "User-agent: *\nDisallow: /private/\nAllow: /private/public-page\n"
        assert _is_path_allowed(robots, "/private/public-page") is True
        assert _is_path_allowed(robots, "/private/secret") is False

    def test_specific_user_agent(self):
        robots = "User-agent: Karna\nDisallow: /no-karna/\n\nUser-agent: *\nDisallow: /no-bots/\n"
        assert _is_path_allowed(robots, "/no-karna/page", "Karna/0.1.0") is False
        assert _is_path_allowed(robots, "/no-bots/page", "Karna/0.1.0") is False

    def test_comments_ignored(self):
        robots = "# This is a comment\nUser-agent: * # all bots\nDisallow: /admin/ # admin area\n"
        assert _is_path_allowed(robots, "/admin/panel") is False
        assert _is_path_allowed(robots, "/public") is True


# ======================================================================= #
#  HTML Text Extraction
# ======================================================================= #


class TestHTMLExtraction:
    def test_strips_tags(self):
        html = "<p>Hello <b>world</b></p>"
        text = _extract_text_basic(html)
        assert "Hello" in text
        assert "world" in text
        assert "<" not in text

    def test_removes_script_and_style(self):
        html = (
            "<html><head><style>body{color:red}</style></head>"
            "<body><script>alert('x')</script><p>Content here</p></body></html>"
        )
        text = _extract_text_basic(html)
        assert "Content here" in text
        assert "alert" not in text
        assert "color:red" not in text

    def test_decodes_entities(self):
        html = "<p>5 &gt; 3 &amp; 2 &lt; 4</p>"
        text = _extract_text_basic(html)
        assert "5 > 3 & 2 < 4" in text

    def test_empty_html(self):
        assert _extract_text_basic("") == ""


# ======================================================================= #
#  Truncation
# ======================================================================= #


class TestTruncation:
    def test_short_text_unchanged(self):
        text = "Hello world."
        assert _truncate_at_boundary(text, 100) == text

    def test_truncates_at_sentence(self):
        text = "First sentence. Second sentence. Third sentence."
        result = _truncate_at_boundary(text, 35)
        assert result.startswith("First sentence. Second sentence.")
        assert "[... truncated]" in result

    def test_truncates_long_text(self):
        text = "A" * 10000
        result = _truncate_at_boundary(text, 100)
        assert len(result) < 200  # some overhead from the truncated marker


# ======================================================================= #
#  WebFetchTool unit tests
# ======================================================================= #


class TestWebFetchTool:
    @pytest.mark.asyncio
    async def test_rejects_private_url(self):
        tool = WebFetchTool()
        result = await tool.execute(url="http://127.0.0.1/secret")
        assert "[error]" in result
        assert "private" in result.lower() or "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_rejects_empty_url(self):
        tool = WebFetchTool()
        result = await tool.execute(url="")
        assert "[error]" in result

    @pytest.mark.asyncio
    async def test_rejects_ftp_scheme(self):
        tool = WebFetchTool()
        result = await tool.execute(url="ftp://ftp.example.com/file")
        assert "[error]" in result


# ======================================================================= #
#  WebSearchTool unit tests
# ======================================================================= #


class TestWebSearchTool:
    @pytest.mark.asyncio
    async def test_empty_query_error(self):
        tool = WebSearchTool()
        result = await tool.execute(query="")
        assert "[error]" in result

    @pytest.mark.asyncio
    async def test_empty_whitespace_query_error(self):
        tool = WebSearchTool()
        result = await tool.execute(query="   ")
        assert "[error]" in result


# ======================================================================= #
#  Registry integration
# ======================================================================= #


class TestWebToolsRegistry:
    def test_get_web_search_tool(self):
        tool = get_tool("web_search")
        assert tool.name == "web_search"

    def test_get_web_fetch_tool(self):
        tool = get_tool("web_fetch")
        assert tool.name == "web_fetch"

    def test_all_tools_includes_web(self):
        tools = get_all_tools()
        names = {t.name for t in tools}
        assert "web_search" in names
        assert "web_fetch" in names


# ======================================================================= #
#  Live network tests (skipped if no network)
# ======================================================================= #


def _has_network() -> bool:
    """Check if we have network connectivity."""
    import socket

    try:
        socket.create_connection(("1.1.1.1", 53), timeout=3)
        return True
    except OSError:
        return False


_NETWORK_AVAILABLE = _has_network()


@pytest.mark.skipif(not _NETWORK_AVAILABLE, reason="No network connectivity")
class TestWebSearchLive:
    @pytest.mark.asyncio
    async def test_duckduckgo_search(self):
        tool = WebSearchTool()
        result = await tool.execute(query="python asyncio", num_results=5)
        assert "python" in result.lower() or "asyncio" in result.lower()
        assert "URL:" in result


@pytest.mark.skipif(not _NETWORK_AVAILABLE, reason="No network connectivity")
class TestWebFetchLive:
    @pytest.mark.asyncio
    async def test_fetch_example_com(self):
        tool = WebFetchTool()
        result = await tool.execute(url="https://example.com")
        assert "Example Domain" in result or "example" in result.lower()

    @pytest.mark.asyncio
    async def test_fetch_with_max_length(self):
        tool = WebFetchTool()
        result = await tool.execute(url="https://example.com", max_length=100)
        assert len(result) < 200  # some overhead from truncation marker
