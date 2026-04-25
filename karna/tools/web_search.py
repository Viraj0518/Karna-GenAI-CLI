"""Web search tool — privacy-first, model-agnostic web search.

Cascading search backends (no API key required by default):
1. DuckDuckGo HTML scraping (default, no key)
2. Brave Search API (optional, via BRAVE_SEARCH_API_KEY)
3. SearXNG (optional, via KARNA_SEARXNG_URL)

PRIVACY: This tool sends requests to external web servers.
- Search queries go to DuckDuckGo (default) or user-configured search engine
- No data is sent to Karna developers, no telemetry, no analytics
- All results are processed locally and kept in conversation context only
"""

from __future__ import annotations

import html
import os
import re
from typing import Any

import httpx

from karna.prompts.cc_tool_prompts import CC_TOOL_PROMPTS
from karna.tools.base import BaseTool

_USER_AGENT = "Nellie/0.1.3 (Karna AI assistant)"
_DEFAULT_TIMEOUT = 15  # seconds
_MAX_RESULTS_DEFAULT = 10


# ======================================================================= #
#  DuckDuckGo HTML scraping
# ======================================================================= #

_DDG_URL = "https://html.duckduckgo.com/html/"

# Each result block in DDG HTML has class "result__a" for the link,
# "result__snippet" for the snippet, etc.
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>'
    r".*?"
    r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)

# Fallback: simpler pattern that catches title + url at minimum
_DDG_LINK_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
    re.DOTALL,
)


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return text.strip()


def _extract_ddg_url(raw_url: str) -> str:
    """Extract the actual URL from DDG's redirect wrapper."""
    # DDG wraps URLs like //duckduckgo.com/l/?uddg=<encoded>&rut=...
    match = re.search(r"uddg=([^&]+)", raw_url)
    if match:
        from urllib.parse import unquote

        return unquote(match.group(1))
    return raw_url


async def _search_duckduckgo(query: str, num_results: int) -> list[dict[str, str]]:
    """Scrape DuckDuckGo HTML search results."""
    async with httpx.AsyncClient(
        headers={"User-Agent": _USER_AGENT},
        timeout=_DEFAULT_TIMEOUT,
        follow_redirects=True,
    ) as client:
        resp = await client.post(_DDG_URL, data={"q": query, "b": ""})
        resp.raise_for_status()
        body = resp.text

    results: list[dict[str, str]] = []

    # Try full pattern first (title + snippet)
    for match in _DDG_RESULT_RE.finditer(body):
        if len(results) >= num_results:
            break
        url = _extract_ddg_url(match.group(1))
        title = _strip_tags(match.group(2))
        snippet = _strip_tags(match.group(3))
        if url and title:
            results.append({"title": title, "url": url, "snippet": snippet})

    # Fallback if full pattern didn't work
    if not results:
        for match in _DDG_LINK_RE.finditer(body):
            if len(results) >= num_results:
                break
            url = _extract_ddg_url(match.group(1))
            title = _strip_tags(match.group(2))
            if url and title and not url.startswith("/"):
                results.append({"title": title, "url": url, "snippet": ""})

    return results


# ======================================================================= #
#  Brave Search API
# ======================================================================= #

_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


async def _search_brave(query: str, num_results: int, api_key: str) -> list[dict[str, str]]:
    """Query Brave Search API."""
    async with httpx.AsyncClient(
        headers={
            "X-Subscription-Token": api_key,
            "Accept": "application/json",
        },
        timeout=_DEFAULT_TIMEOUT,
    ) as client:
        resp = await client.get(
            _BRAVE_URL,
            params={"q": query, "count": min(num_results, 20)},
        )
        resp.raise_for_status()
        data = resp.json()

    results: list[dict[str, str]] = []
    for item in data.get("web", {}).get("results", []):
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("description", ""),
            }
        )
        if len(results) >= num_results:
            break

    return results


# ======================================================================= #
#  SearXNG
# ======================================================================= #


async def _search_searxng(query: str, num_results: int, base_url: str) -> list[dict[str, str]]:
    """Query a local SearXNG instance."""
    async with httpx.AsyncClient(
        headers={"User-Agent": _USER_AGENT},
        timeout=_DEFAULT_TIMEOUT,
    ) as client:
        resp = await client.get(
            f"{base_url.rstrip('/')}/search",
            params={"q": query, "format": "json"},
        )
        resp.raise_for_status()
        data = resp.json()

    results: list[dict[str, str]] = []
    for item in data.get("results", []):
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
            }
        )
        if len(results) >= num_results:
            break

    return results


# ======================================================================= #
#  WebSearchTool
# ======================================================================= #


def _format_results(results: list[dict[str, str]], backend: str) -> str:
    """Format search results as readable text."""
    if not results:
        return "(no results found)"

    lines = [f"Search results ({backend}):"]
    for i, r in enumerate(results, 1):
        lines.append(f"\n{i}. {r['title']}")
        lines.append(f"   URL: {r['url']}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet']}")

    return "\n".join(lines)


class WebSearchTool(BaseTool):
    """Search the web using privacy-respecting backends.

    PRIVACY: This tool sends the search query to DuckDuckGo (default),
    Brave Search, or a user-configured SearXNG instance. No other data
    is transmitted. No telemetry. No logging to external services.
    """

    name = "web_search"
    description = "Search the web. Returns titles, URLs, and snippets."
    cc_prompt = CC_TOOL_PROMPTS["web_search"]
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "num_results": {
                "type": "integer",
                "default": 10,
                "description": "Number of results to return",
            },
        },
        "required": ["query"],
    }

    async def execute(self, **kwargs: Any) -> str:
        query: str = kwargs["query"]
        num_results: int = kwargs.get("num_results", _MAX_RESULTS_DEFAULT)

        if not query.strip():
            return "[error] Empty search query."

        # Cascade: SearXNG > Brave > DuckDuckGo
        searxng_url = os.environ.get("KARNA_SEARXNG_URL")
        brave_key = os.environ.get("BRAVE_SEARCH_API_KEY")

        errors: list[str] = []

        # 1. Try SearXNG if configured
        if searxng_url:
            try:
                results = await _search_searxng(query, num_results, searxng_url)
                return _format_results(results, "SearXNG")
            except Exception as exc:
                errors.append(f"SearXNG: {exc}")

        # 2. Try Brave if key available
        if brave_key:
            try:
                results = await _search_brave(query, num_results, brave_key)
                return _format_results(results, "Brave Search")
            except Exception as exc:
                errors.append(f"Brave: {exc}")

        # 3. DuckDuckGo (default)
        try:
            results = await _search_duckduckgo(query, num_results)
            return _format_results(results, "DuckDuckGo")
        except Exception as exc:
            errors.append(f"DuckDuckGo: {exc}")

        return "[error] All search backends failed:\n" + "\n".join(f"  - {e}" for e in errors)
