"""Pre-execution safety checks for agent tool calls.

Validates tool arguments before execution to block dangerous
operations: destructive shell commands, access to sensitive file
paths, and requests to private network addresses.

Ported from cc-src BashTool security patterns with attribution
to the Anthropic Claude Code codebase.
"""

from __future__ import annotations

import ipaddress
import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from karna.tools.base import BaseTool


# ----------------------------------------------------------------------- #
#  Dangerous command patterns
# ----------------------------------------------------------------------- #

_DANGEROUS_COMMAND_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Filesystem destruction
    (re.compile(r"\brm\s+(-[rRf]+\s+|--recursive\s+)/\s*$"), "recursive delete of root"),
    (re.compile(r"\brm\s+(-[rRf]+\s+|--recursive\s+)/\s"), "recursive delete of root"),
    (re.compile(r"\brm\s+(-[rRf]+\s+|--recursive\s+)~\s*$"), "recursive delete of home"),
    # Block device writes
    (re.compile(r"\bdd\b.*\bof=/dev/[sh]d"), "direct write to block device"),
    (re.compile(r"\bmkfs\b"), "filesystem format"),
    (re.compile(r">\s*/dev/[sh]d"), "redirect to block device"),
    # Fork bomb
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;"), "fork bomb"),
    # Remote code execution
    (re.compile(r"\bcurl\b.*\|\s*(ba)?sh"), "piping remote script to shell"),
    (re.compile(r"\bwget\b.*\|\s*(ba)?sh"), "piping remote script to shell"),
    # Dangerous permission changes
    (re.compile(r"\bchmod\s+(-R\s+)?777\s+/\s*$"), "recursive 777 on root"),
    (re.compile(r"\bchown\s+-R\s+.*\s+/\s*$"), "recursive chown of root"),
    # Git force push to main
    (re.compile(r"\bgit\s+push\s+.*--force.*\b(main|master)\b"), "force push to main/master"),
    (re.compile(r"\bgit\s+push\s+-f\s+.*\b(main|master)\b"), "force push to main/master"),
]


def check_dangerous_command(command: str) -> str | None:
    """Return a warning string if *command* matches a dangerous pattern.

    Returns ``None`` if the command appears safe.
    """
    for pattern, reason in _DANGEROUS_COMMAND_PATTERNS:
        if pattern.search(command):
            return reason
    return None


# ----------------------------------------------------------------------- #
#  Sensitive path detection
# ----------------------------------------------------------------------- #

_SENSITIVE_PATHS: list[str] = [
    "/etc/shadow",
    "/etc/passwd",
    "/etc/sudoers",
    "/etc/ssh/",
    "/.ssh/",
    "/.gnupg/",
    "/.aws/credentials",
    "/.aws/config",
    "/.env",
    "/credentials.json",
    "/service-account.json",
    "/.karna/credentials/",
]

_SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\.pem$"),
    re.compile(r"\.key$"),
    re.compile(r"id_rsa"),
    re.compile(r"id_ed25519"),
]


def is_safe_path(path: str) -> bool:
    """Return True if *path* does not point to a known sensitive location."""
    normalized = path.replace("\\", "/")

    for sensitive in _SENSITIVE_PATHS:
        if sensitive in normalized:
            return False

    for pattern in _SENSITIVE_PATTERNS:
        if pattern.search(normalized):
            return False

    return True


# ----------------------------------------------------------------------- #
#  URL safety (block private networks)
# ----------------------------------------------------------------------- #

_PRIVATE_HOST_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^localhost$", re.IGNORECASE),
    re.compile(r"^127\."),
    re.compile(r"^0\.0\.0\.0$"),
    re.compile(r"^::1$"),
    re.compile(r"^10\."),
    re.compile(r"^172\.(1[6-9]|2\d|3[01])\."),
    re.compile(r"^192\.168\."),
    re.compile(r"\.local$", re.IGNORECASE),
    re.compile(r"^metadata\.google\.internal$", re.IGNORECASE),
    re.compile(r"^169\.254\.169\.254$"),
]


def is_safe_url(url: str) -> bool:
    """Return True if *url* does not point to a private/internal network.

    Blocks localhost, RFC-1918, link-local, and cloud metadata endpoints.
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
    except Exception:
        return False

    if not host:
        return False

    for pattern in _PRIVATE_HOST_PATTERNS:
        if pattern.search(host):
            return False

    # Try to parse as IP and check private ranges
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            return False
    except ValueError:
        pass  # Not an IP address — hostname, already checked patterns

    return True


# ----------------------------------------------------------------------- #
#  Main pre-execution check
# ----------------------------------------------------------------------- #


async def pre_tool_check(
    tool: "BaseTool",
    args: dict,
) -> tuple[bool, str | None]:
    """Check if tool execution is safe.

    Returns ``(proceed, warning_message)``.  If ``proceed`` is False,
    the tool should not be executed and ``warning_message`` explains why.
    """
    if tool.name == "bash":
        command = args.get("command", "")
        warning = check_dangerous_command(command)
        if warning:
            return False, f"Dangerous command blocked: {warning}"

    if tool.name in ("read", "edit", "write"):
        path = args.get("path", args.get("file_path", ""))
        if not is_safe_path(path):
            return False, f"Blocked: accessing sensitive path: {path}"

    if tool.name == "web_fetch":
        url = args.get("url", "")
        if not is_safe_url(url):
            return False, f"Blocked: URL points to private/internal network: {url}"

    return True, None
