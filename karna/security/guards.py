"""Core security guards for Karna.

- Path traversal prevention
- Secret detection and scrubbing
- SSRF guard for web fetch
- Dangerous command detection for bash tool
"""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from urllib.parse import urlparse

# ------------------------------------------------------------------ #
#  Path traversal guard
# ------------------------------------------------------------------ #

# Sensitive locations that tools should NEVER touch. Superset of the
# lists previously scattered across ``agents/safety.py`` — kept here as
# the single source of truth (MEDIUM-4 fix).
_SENSITIVE_PATHS: list[str] = [
    "/etc/shadow",
    "/etc/passwd",
    "/etc/sudoers",
]

# Any resolved path that is *under* one of these roots is rejected.
# Uses ``Path.is_relative_to`` (not string prefix) so Windows paths with
# backslashes are handled correctly (MEDIUM-1 fix).
_SENSITIVE_PREFIXES: list[str] = [
    "~/.ssh",
    "~/.gnupg",
    "~/.aws",
    "~/.karna/credentials",
    "/etc/ssh",
    "/dev",
    "/proc",
    "/sys",
]

# Basename-level patterns — paths with these suffixes/names are always
# rejected regardless of location (e.g., id_rsa anywhere on disk).
_SENSITIVE_NAME_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\.pem$"),
    re.compile(r"\.key$"),
    re.compile(r"(^|[\\/])id_rsa(\.|$)"),
    re.compile(r"(^|[\\/])id_ed25519(\.|$)"),
    re.compile(r"(^|[\\/])credentials\.json$"),
    re.compile(r"(^|[\\/])service-account\.json$"),
    re.compile(r"(^|[\\/])\.env(\.|$)"),
]


def is_safe_path(
    path: str | Path,
    allowed_roots: list[Path] | None = None,
) -> bool:
    """Reject paths that escape the working directory or hit sensitive locations.

    Blocked:
    - /etc/shadow, /etc/passwd, /etc/sudoers
    - ~/.ssh/, ~/.gnupg/, ~/.aws/
    - ~/.karna/credentials/ (tools should NEVER read credential files)
    - Any path with .. that escapes cwd
    - /dev/, /proc/, /sys/, /etc/ssh/
    - Files named id_rsa, id_ed25519, *.pem, *.key, credentials.json,
      service-account.json, .env

    Uses ``Path.is_relative_to`` so Windows backslash paths work correctly.

    Parameters
    ----------
    path:
        The path to check (may be relative or absolute).
    allowed_roots:
        If provided, the resolved path must be under one of these roots.
        If ``None``, defaults to ``[Path.cwd()]``.
    """
    raw = os.path.expanduser(str(path))
    # Windows + Git Bash emit ``/c/Users/...`` style paths for the C:
    # drive. Python's pathlib treats the leading ``/c`` as a directory
    # name (resolves to ``C:\c\Users\...``) which breaks every
    # allowed_roots containment check when the agent hands back
    # bash-shaped paths. Normalise ``/<letter>/<rest>`` → ``<letter>:/<rest>``
    # on Windows before resolving.
    if os.name == "nt":
        m = re.match(r"^[/\\]([a-zA-Z])[/\\](.*)$", raw)
        if m:
            raw = f"{m.group(1).upper()}:/{m.group(2)}"
    try:
        resolved = Path(raw).resolve()
    except (ValueError, OSError):
        return False

    # Block exact sensitive paths
    for sp in _SENSITIVE_PATHS:
        try:
            if resolved == Path(sp).resolve():
                return False
        except (OSError, ValueError):
            continue

    # Block sensitive prefixes using Path.is_relative_to (not string
    # prefix) so Windows path separators normalise correctly.
    for sensitive_root in _SENSITIVE_PREFIXES:
        try:
            root = Path(os.path.expanduser(sensitive_root)).resolve()
        except (OSError, ValueError):
            continue
        try:
            if resolved.is_relative_to(root):
                return False
        except (OSError, ValueError):
            continue

    # Block basename patterns (id_rsa, *.pem, etc.) anywhere on disk.
    resolved_str = str(resolved)
    for pattern in _SENSITIVE_NAME_PATTERNS:
        if pattern.search(resolved_str):
            return False

    # If allowed_roots provided, enforce containment
    if allowed_roots is not None:
        roots = allowed_roots
    else:
        roots = [Path.cwd()]

    for root in roots:
        try:
            if resolved.is_relative_to(root.resolve()):
                return True
        except (OSError, ValueError):
            continue

    return False


# ------------------------------------------------------------------ #
#  Secret detection / scrubbing
# ------------------------------------------------------------------ #

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # OpenAI / generic sk- keys
    (re.compile(r"sk-[a-zA-Z0-9]{20,}"), "<REDACTED_SECRET>"),
    # OpenRouter v1 keys
    (re.compile(r"sk-or-v1-[a-zA-Z0-9]+"), "<REDACTED_SECRET>"),
    # Anthropic keys (covers sk-ant-api03-* and generic sk-ant-*)
    (re.compile(r"sk-ant-[a-zA-Z0-9_\-]+"), "<REDACTED_SECRET>"),
    # GitHub personal access tokens (classic + fine-grained)
    (re.compile(r"ghp_[a-zA-Z0-9]{36,}"), "<REDACTED_SECRET>"),
    (re.compile(r"github_pat_[a-zA-Z0-9_]{82}"), "<REDACTED_SECRET>"),
    # Google API keys (Firebase, Maps, Cloud, GenAI) — 39 chars total
    (re.compile(r"AIzaSy[a-zA-Z0-9_\-]{33}"), "<REDACTED_SECRET>"),
    # AWS access key IDs
    (re.compile(r"AKIA[0-9A-Z]{16}"), "<REDACTED_SECRET>"),
    # Azure / generic 32-hex API keys often passed via
    # "api-key: <hex>" or "Ocp-Apim-Subscription-Key: <hex>" headers.
    # Match only when preceded by a clear key/header label to avoid
    # hashing false positives (git SHAs, MD5s, etc.).
    (
        re.compile(
            r"(?i)(api[_-]?key|ocp-apim-subscription-key|x-api-key)"
            r"\s*[:=]\s*['\"]?[0-9a-f]{32}['\"]?"
        ),
        r"\1: <REDACTED_SECRET>",
    ),
    # PEM private keys
    (re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"), "<REDACTED_SECRET>"),
    # Bearer tokens
    (re.compile(r"Bearer [a-zA-Z0-9._\-]{20,}"), "Bearer <REDACTED_SECRET>"),
    # HuggingFace tokens
    (re.compile(r"hf_[a-zA-Z0-9]{20,}"), "<REDACTED_SECRET>"),
]


def scrub_secrets(text: str) -> str:
    """Remove anything that looks like a secret from text.

    Patterns matched:
    - sk-[a-zA-Z0-9]{20,}
    - sk-or-v1-[a-zA-Z0-9]+
    - sk-ant-[a-zA-Z0-9_\\-]+  (Anthropic, covers api03-*)
    - ghp_[a-zA-Z0-9]{36,}  (GitHub classic PAT)
    - github_pat_[a-zA-Z0-9_]{82}  (GitHub fine-grained PAT)
    - AIzaSy[a-zA-Z0-9_\\-]{33}  (Google API keys)
    - AKIA[0-9A-Z]{16}
    - Azure-style 32-hex keys in ``api-key:`` / ``x-api-key`` headers
    - -----BEGIN (RSA |EC |)PRIVATE KEY-----
    - Bearer [a-zA-Z0-9._-]{20,}
    - hf_[a-zA-Z0-9]{20,}

    Replaces with ``<REDACTED_SECRET>``.
    """
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ------------------------------------------------------------------ #
#  SSRF guard (for web_fetch)
# ------------------------------------------------------------------ #

# Private/reserved IPv4 networks
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("0.0.0.0/8"),
]

# Private/reserved IPv6 networks
_PRIVATE_NETWORKS_V6 = [
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # unique local
    ipaddress.ip_network("fe80::/10"),  # link-local
]


def _is_private_ip(host: str) -> bool:
    """Check if *host* resolves to a private/internal IP."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False

    networks = _PRIVATE_NETWORKS if addr.version == 4 else _PRIVATE_NETWORKS_V6
    return any(addr in net for net in networks)


def is_safe_url(url: str) -> bool:
    """Reject URLs targeting private/internal networks.

    Blocks:
    - http://127.0.0.1, http://10.x.x.x, http://192.168.x.x, etc.
    - http://[::1], fc00::/7, fe80::/10
    - http://localhost
    - Non-HTTP(S) schemes (file://, ftp://, etc.)
    - URLs without a hostname

    Allows:
    - Any public HTTPS URL
    - Any public HTTP URL (with warning in logs)
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    # Only allow http/https schemes
    if parsed.scheme not in ("http", "https"):
        return False

    host = parsed.hostname
    if not host:
        return False

    # Block localhost by name
    if host in ("localhost", "localhost.localdomain"):
        return False

    # Block private IPs
    if _is_private_ip(host):
        return False

    return True


# ------------------------------------------------------------------ #
#  Dangerous command detection (for bash tool)
# ------------------------------------------------------------------ #

DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"rm\s+(-\w*[rR]\w*\s+|--recursive\s+)/(\s|$)"), "recursive delete of root filesystem"),
    (re.compile(r"\bdd\b.*\bof=/dev/"), "direct write to block device"),
    (re.compile(r"\bchmod\s+(-\w*R\w*\s+)?777\s+/(\s|$)"), "chmod 777 on root filesystem"),
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;\s*:"), "fork bomb"),
    (re.compile(r"\bmkfs\."), "filesystem format command"),
    (re.compile(r">\s*/dev/sd"), "redirect to block device"),
    (re.compile(r"\bcurl\b.*\|\s*(ba)?sh"), "piping remote script to shell"),
    (re.compile(r"\bwget\b.*\|\s*(ba)?sh"), "piping remote script to shell"),
]


def check_dangerous_command(cmd: str) -> str | None:
    """Return warning string if *cmd* matches dangerous patterns, None if safe.

    Checks for:
    - ``rm -rf /``
    - ``dd ... of=/dev/``
    - ``chmod 777 /``
    - fork bombs
    - ``mkfs.*``
    - redirect to block devices
    - ``curl ... | sh`` / ``wget ... | sh``
    """
    for pattern, reason in DANGEROUS_PATTERNS:
        if pattern.search(cmd):
            return f"Dangerous command detected: {reason}"
    return None
