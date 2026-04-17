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
from urllib.parse import urlparse, urlunparse

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


def _is_private_ip(ip: str) -> bool:
    """Return True if *ip* is private, loopback, link-local, reserved, multicast, or unparseable.

    Uses the stdlib ``ipaddress`` module, which correctly normalises
    octal/hex/decimal-packed encodings (e.g. ``0177.0.0.1`` ->
    ``127.0.0.1``) and handles IPv6 equivalents. On parse failure we
    fail closed (return True) so bogus input is blocked rather than
    allowed through.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # Unparseable = block (fail closed)

    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _resolve_and_pin(url: str) -> tuple[str, str]:
    """Resolve the URL's host once and return a URL rewritten to connect to that IP.

    This mitigates DNS rebinding: the guard resolves the host, verifies
    the IP is public, then pins the downstream httpx connection to the
    exact IP that was checked. A second DNS lookup by httpx cannot
    substitute a private IP.

    Returns
    -------
    (pinned_url, original_host)
        ``pinned_url`` has the IP in its netloc and is what httpx should
        fetch. ``original_host`` is the original hostname — the caller
        must send it as the ``Host`` header so TLS SNI / vhost routing
        still work.

    Raises
    ------
    ValueError
        If the URL has no host, fails to resolve, or resolves to a
        private / loopback / link-local / reserved IP.
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise ValueError("URL has no host")

    # Single DNS resolution. Use AF_UNSPEC so IPv6-only hosts still work;
    # we validate whichever family we got back.
    try:
        addr_info = socket.getaddrinfo(host, None)
    except (socket.gaierror, OSError) as exc:
        raise ValueError(f"DNS resolution failed for {host}: {exc}") from exc

    if not addr_info:
        raise ValueError(f"No DNS records for {host}")

    # Validate EVERY returned address — if any one is private, block.
    # Then pin to the first public address.
    pinned_ip: str | None = None
    for info in addr_info:
        ip_str = info[4][0]
        # Strip IPv6 scope ids (fe80::1%eth0) before ipaddress parses.
        if "%" in ip_str:
            ip_str = ip_str.split("%", 1)[0]
        if _is_private_ip(ip_str):
            raise ValueError(
                f"Host {host} resolves to non-public address {ip_str} — blocked"
            )
        if pinned_ip is None:
            pinned_ip = ip_str

    assert pinned_ip is not None

    # Rebuild the netloc with the pinned IP. For IPv6, bracket the address.
    try:
        addr_obj = ipaddress.ip_address(pinned_ip)
        ip_netloc = f"[{pinned_ip}]" if addr_obj.version == 6 else pinned_ip
    except ValueError:
        ip_netloc = pinned_ip

    port_suffix = f":{parsed.port}" if parsed.port else ""
    pinned = urlunparse(parsed._replace(netloc=f"{ip_netloc}{port_suffix}"))
    return pinned, host


def is_safe_url(url: str) -> bool:
    """Reject URLs pointing to private/internal networks.

    Returns True if the URL is safe to fetch, False otherwise.

    Note: this is a pre-check used by callers that want a boolean
    answer (e.g. ``agents/safety.py``). The fetch path additionally
    uses ``_resolve_and_pin`` at connect time to defeat DNS rebinding.
    """
    parsed = urlparse(url)

    # Scheme check
    if parsed.scheme not in ("http", "https"):
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    # Resolve hostname to IP and check every returned address.
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, ValueError, OSError):
        return False

    if not infos:
        return False

    for info in infos:
        ip_str = info[4][0]
        if "%" in ip_str:
            ip_str = ip_str.split("%", 1)[0]
        if _is_private_ip(ip_str):
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
            line = line[:line.index("#")].strip()
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
        "Fetch a web page and extract its readable text content. "
        "Use after web_search to read a specific result."
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

        parsed = urlparse(url)

        # Scheme check — cheap rejection before DNS.
        if parsed.scheme not in ("http", "https"):
            return "[error] URL blocked: only http:// and https:// are supported."

        # SSRF guard: resolve DNS once, validate the IP, and pin the
        # connection to that IP. This defeats DNS rebinding attacks
        # where the attacker's resolver returns a public IP for the
        # pre-fetch check and a private IP for the actual fetch.
        try:
            pinned_url, original_host = _resolve_and_pin(url)
        except ValueError as exc:
            return f"[error] URL blocked: {exc}"

        # robots.txt check
        try:
            robots_txt = await _fetch_robots_txt(parsed.scheme, parsed.hostname or "", parsed.port)
            if robots_txt is not None:
                if not _is_path_allowed(robots_txt, parsed.path or "/", _USER_AGENT):
                    return f"[blocked] robots.txt disallows access to {parsed.path}"
        except Exception:
            pass  # On robots.txt failure, proceed (be permissive)

        # Fetch the page against the pinned IP, preserving the original
        # Host header so virtual-host routing still works. For HTTPS we
        # also pass the ``sni_hostname`` request extension so TLS SNI
        # and certificate-hostname verification use the original name
        # rather than the raw IP.
        port_for_host_header = f":{parsed.port}" if parsed.port else ""
        host_header = f"{original_host}{port_for_host_header}"
        request_extensions: dict[str, Any] = {}
        if parsed.scheme == "https":
            request_extensions["sni_hostname"] = original_host
        try:
            async with httpx.AsyncClient(
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "text/html, text/plain, application/json",
                    "Host": host_header,
                },
                timeout=_DEFAULT_TIMEOUT,
                follow_redirects=True,
                max_redirects=5,
            ) as client:
                resp = await client.get(
                    pinned_url,
                    extensions=request_extensions or None,
                )
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
            return f"[error] Unsupported content type: {mime}. Only text/html, text/plain, and application/json are supported."

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
