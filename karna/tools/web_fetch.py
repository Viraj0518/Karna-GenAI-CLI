"""Web fetch tool — fetch and extract readable content from URLs.

Includes SSRF protection, robots.txt respect, and content-type
guards. Uses trafilatura for HTML extraction when available, falls
back to basic regex tag stripping.

PRIVACY: This tool sends requests to external web servers.
- Fetch requests go to the specified URL
- No data is sent to Karna developers, no telemetry, no analytics
- All results are processed locally and kept in conversation context only
"""

from __future__ import annotations

import html
import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from karna.tools.base import BaseTool

_USER_AGENT = "Nellie/0.1.0 (Karna AI assistant)"
_DEFAULT_TIMEOUT = 15  # seconds
_MAX_RESPONSE_BYTES = 1_048_576  # 1 MB
_MAX_LENGTH_DEFAULT = 5000

# Content types we are willing to process.
_ALLOWED_CONTENT_TYPES = {"text/html", "text/plain", "application/json"}


# ======================================================================= #
#  SSRF Guard
# ======================================================================= #


def is_safe_url(url: str) -> bool:
    """Reject URLs pointing to private/internal networks.

    Returns True if the URL is safe to fetch, False otherwise.
    """
    parsed = urlparse(url)

    # Scheme check
    if parsed.scheme not in ("http", "https"):
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    # Resolve hostname to IP and check
    try:
        infos = socket.getaddrinfo(hostname, None)
        for info in infos:
            ip_str = info[4][0]
            addr = ipaddress.ip_address(ip_str)
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                return False
    except (socket.gaierror, ValueError, OSError):
        return False

    return True


# ======================================================================= #
#  robots.txt parsing (minimal, no dependency)
# ======================================================================= #

_ROBOTS_CACHE: dict[str, str | None] = {}


async def _fetch_robots_txt(scheme: str, hostname: str, port: int | None) -> str | None:
    """Fetch robots.txt for a host. Returns content or None."""
    port_part = f":{port}" if port and port not in (80, 443) else ""
    robots_url = f"{scheme}://{hostname}{port_part}/robots.txt"

    cache_key = f"{scheme}://{hostname}{port_part}"
    if cache_key in _ROBOTS_CACHE:
        return _ROBOTS_CACHE[cache_key]

    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT},
            timeout=5,
            follow_redirects=True,
        ) as client:
            resp = await client.get(robots_url)
            if resp.status_code == 200:
                content = resp.text
                _ROBOTS_CACHE[cache_key] = content
                return content
    except Exception:
        pass

    _ROBOTS_CACHE[cache_key] = None
    return None


def _is_path_allowed(robots_txt: str, path: str, user_agent: str = "*") -> bool:
    """Check if path is allowed by robots.txt rules.

    Simple parser: matches User-agent: * and our specific agent name.
    Returns True if allowed, False if disallowed.
    """
    # Parse into (user_agent, rules) sections
    current_agents: list[str] = []
    rules: list[tuple[str, list[tuple[str, str]]]] = []
    current_rules: list[tuple[str, str]] = []

    for line in robots_txt.splitlines():
        line = line.strip()
        # Strip comments
        if "#" in line:
            line = line[: line.index("#")].strip()
        if not line:
            continue

        if line.lower().startswith("user-agent:"):
            agent = line.split(":", 1)[1].strip().lower()
            if current_rules and current_agents:
                for a in current_agents:
                    rules.append((a, list(current_rules)))
                current_rules = []
                current_agents = []
            current_agents.append(agent)
        elif line.lower().startswith("disallow:"):
            disallow_path = line.split(":", 1)[1].strip()
            if disallow_path:
                current_rules.append(("disallow", disallow_path))
        elif line.lower().startswith("allow:"):
            allow_path = line.split(":", 1)[1].strip()
            if allow_path:
                current_rules.append(("allow", allow_path))

    # Flush last section
    if current_agents and current_rules:
        for a in current_agents:
            rules.append((a, list(current_rules)))

    # Check rules for matching user agents
    ua_lower = user_agent.lower()
    matching_rules: list[tuple[str, str]] = []
    for agent, agent_rules in rules:
        if agent == "*" or agent in ua_lower:
            matching_rules.extend(agent_rules)

    if not matching_rules:
        return True  # No rules = allowed

    # Find the most specific matching rule
    best_match: tuple[str, str] | None = None
    best_length = -1

    for directive, rule_path in matching_rules:
        if path.startswith(rule_path) and len(rule_path) > best_length:
            best_match = (directive, rule_path)
            best_length = len(rule_path)

    if best_match is None:
        return True

    return best_match[0] == "allow"


# ======================================================================= #
#  HTML to text extraction
# ======================================================================= #


def _extract_text_basic(html_content: str) -> str:
    """Basic HTML to text extraction using regex (no dependency)."""
    # Remove script and style blocks
    text = re.sub(r"<script[^>]*>.*?</script>", "", html_content, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<noscript[^>]*>.*?</noscript>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<nav[^>]*>.*?</nav>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<footer[^>]*>.*?</footer>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<header[^>]*>.*?</header>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # Add newlines before block elements
    text = re.sub(r"<(?:p|div|h[1-6]|li|br|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)

    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", "", text)

    # Decode entities
    text = html.unescape(text)

    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def _extract_text(html_content: str) -> str:
    """Extract readable text from HTML. Uses trafilatura if available."""
    try:
        import trafilatura

        result = trafilatura.extract(html_content)
        if result:
            return result
    except ImportError:
        pass
    except Exception:
        pass

    return _extract_text_basic(html_content)


def _truncate_at_boundary(text: str, max_length: int) -> str:
    """Truncate text at the nearest sentence boundary before max_length."""
    if len(text) <= max_length:
        return text

    # Look for sentence boundary near the limit
    truncated = text[:max_length]
    # Find last sentence-ending punctuation
    last_period = max(
        truncated.rfind(". "),
        truncated.rfind(".\n"),
        truncated.rfind("? "),
        truncated.rfind("! "),
    )

    if last_period > max_length * 0.5:  # Only use if we keep at least half
        truncated = truncated[: last_period + 1]
    else:
        # Fall back to last newline
        last_newline = truncated.rfind("\n")
        if last_newline > max_length * 0.5:
            truncated = truncated[:last_newline]

    return truncated + "\n\n[... truncated]"


# ======================================================================= #
#  WebFetchTool
# ======================================================================= #


class WebFetchTool(BaseTool):
    """Fetch a web page and extract its readable text content.

    PRIVACY: This tool sends the fetch request to the specified URL.
    No other data is transmitted. No telemetry. No logging to
    external services.
    """

    name = "web_fetch"
    description = (
        "Fetch a web page and extract its readable text content. Use after web_search to read a specific result."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to fetch",
            },
            "prompt": {
                "type": "string",
                "description": "What to extract from the page (optional, for context only)",
            },
            "max_length": {
                "type": "integer",
                "default": 5000,
                "description": "Max characters to return",
            },
        },
        "required": ["url"],
    }

    async def execute(self, **kwargs: Any) -> str:
        url: str = kwargs["url"]
        max_length: int = kwargs.get("max_length", _MAX_LENGTH_DEFAULT)

        if not url.strip():
            return "[error] Empty URL."

        # SSRF guard
        if not is_safe_url(url):
            return "[error] URL blocked: points to a private/internal network address or uses a disallowed scheme."

        parsed = urlparse(url)

        # robots.txt check
        try:
            robots_txt = await _fetch_robots_txt(parsed.scheme, parsed.hostname or "", parsed.port)
            if robots_txt is not None:
                if not _is_path_allowed(robots_txt, parsed.path or "/", _USER_AGENT):
                    return f"[blocked] robots.txt disallows access to {parsed.path}"
        except Exception:
            pass  # On robots.txt failure, proceed (be permissive)

        # Fetch the page
        try:
            async with httpx.AsyncClient(
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "text/html, text/plain, application/json",
                },
                timeout=_DEFAULT_TIMEOUT,
                follow_redirects=True,
                max_redirects=5,
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except httpx.TimeoutException:
            return f"[error] Request timed out after {_DEFAULT_TIMEOUT}s"
        except httpx.TooManyRedirects:
            return "[error] Too many redirects."
        except httpx.HTTPStatusError as exc:
            return f"[error] HTTP {exc.response.status_code}: {exc.response.reason_phrase}"
        except Exception as exc:
            return f"[error] Failed to fetch URL: {exc}"

        # Content-type guard
        content_type = resp.headers.get("content-type", "")
        mime = content_type.split(";")[0].strip().lower()

        if mime not in _ALLOWED_CONTENT_TYPES:
            return (
                f"[error] Unsupported content type: {mime}. "
                "Only text/html, text/plain, and application/json are supported."
            )

        # Size guard
        raw_bytes = resp.content
        if len(raw_bytes) > _MAX_RESPONSE_BYTES:
            raw_bytes = raw_bytes[:_MAX_RESPONSE_BYTES]

        text = raw_bytes.decode("utf-8", errors="replace")

        # Extract readable content based on content type
        if mime == "text/html":
            text = _extract_text(text)
        elif mime == "application/json":
            # Return JSON as-is (pretty enough for the model)
            pass
        # text/plain: return as-is

        # Truncate
        text = _truncate_at_boundary(text, max_length)

        if not text.strip():
            return "(page returned no readable content)"

        return text
