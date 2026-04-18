"""Memory-safe text scrubbing for Karna.

Cleans text before it is persisted to memory files on disk,
ensuring no secrets, credential paths, or encoded key material
leak into long-term storage.
"""

from __future__ import annotations

import re

from karna.security.guards import scrub_secrets

# Matches file paths containing 'credentials' or '.ssh'
_SENSITIVE_PATH_RE = re.compile(
    r"(?:/[\w.~-]+)*/"
    r"(?:credentials|\.ssh)"
    r"(?:/[\w.~-]+)*"
)

# Base64-encoded blobs >100 chars (likely keys or certs)
_BASE64_BLOB_RE = re.compile(r"[A-Za-z0-9+/]{100,}={0,3}")


def scrub_for_memory(text: str) -> str:
    """Clean text before writing to memory files.

    Steps:
    1. Run ``scrub_secrets()`` to remove API keys / tokens.
    2. Remove file paths containing 'credentials' or '.ssh'.
    3. Remove base64-encoded blobs >100 chars (likely keys).
    """
    # Step 1: API key / token scrubbing
    text = scrub_secrets(text)

    # Step 2: Redact sensitive file paths
    text = _SENSITIVE_PATH_RE.sub("<REDACTED_PATH>", text)

    # Step 3: Redact large base64 blobs
    text = _BASE64_BLOB_RE.sub("<REDACTED_BLOB>", text)

    return text
