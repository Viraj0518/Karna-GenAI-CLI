"""Credential storage for provider API keys.

Credentials are stored as JSON files in ``~/.karna/credentials/``
with mode 0600 for basic filesystem protection.

Security invariants:
- ``os.umask(0o077)`` is set before writing any file.
- All credential files are ``chmod 0600`` after write.
- The credentials directory is ``chmod 0700``.
- Credential values are NEVER printed or logged in full.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CREDENTIALS_DIR = Path.home() / ".karna" / "credentials"


def _ensure_dir() -> None:
    """Create the credentials directory if it doesn't exist.

    Sets the directory mode to 0700 (owner-only access).
    """
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CREDENTIALS_DIR, 0o700)


def save_credential(provider: str, data: dict[str, Any]) -> Path:
    """Write *data* to ``~/.karna/credentials/<provider>.token.json``.

    The file is created with mode 0600 (owner read/write only).
    ``os.umask(0o077)`` is set before writing to prevent race conditions.
    Returns the path to the written file.
    """
    _ensure_dir()
    path = CREDENTIALS_DIR / f"{provider}.token.json"

    old_umask = os.umask(0o077)
    try:
        path.write_text(json.dumps(data, indent=2))
    finally:
        os.umask(old_umask)

    os.chmod(path, 0o600)

    # Log that we saved, but NEVER log the actual key content
    _log_credential_event("saved", provider, data)
    return path


def load_credential(provider: str) -> dict[str, Any]:
    """Read and return the credential dict for *provider*.

    Returns an empty dict if no credential file exists.
    Credential values are never logged in full -- only the first 8 chars.
    """
    path = CREDENTIALS_DIR / f"{provider}.token.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    _log_credential_event("loaded", provider, data)
    return data


def load_credential_pool(provider: str) -> "CredentialPool":
    """Load credentials for *provider* and wrap them in a ``CredentialPool``.

    Handles both single-key (``{"api_key": "..."}`) and multi-key
    (``{"keys": [...], "strategy": "..."}}``) formats transparently.

    Returns an empty pool if no credential file exists.
    """
    from karna.auth.pool import CredentialPool

    data = load_credential(provider)
    return CredentialPool.from_credential_data(provider, data)


def list_credentials() -> list[str]:
    """Return a list of provider names that have saved credentials."""
    _ensure_dir()
    return [
        p.stem.removesuffix(".token")
        for p in CREDENTIALS_DIR.glob("*.token.json")
        if p.is_file()
    ]


def check_credential_permissions() -> list[str]:
    """Check that credential files and directory have safe permissions.

    Returns a list of warning strings. Empty list means everything is fine.
    """
    warnings: list[str] = []
    if not CREDENTIALS_DIR.exists():
        return warnings

    # Check directory permissions
    dir_stat = CREDENTIALS_DIR.stat()
    dir_mode = dir_stat.st_mode & 0o777
    if dir_mode != 0o700:
        warnings.append(
            f"Credentials directory {CREDENTIALS_DIR} has mode "
            f"{oct(dir_mode)} (expected 0700). Run: chmod 700 {CREDENTIALS_DIR}"
        )

    # Check individual credential files
    for cred_file in CREDENTIALS_DIR.glob("*.token.json"):
        file_mode = cred_file.stat().st_mode & 0o777
        if file_mode != 0o600:
            warnings.append(
                f"Credential file {cred_file} has mode "
                f"{oct(file_mode)} (expected 0600). Run: chmod 600 {cred_file}"
            )

    return warnings


def _log_credential_event(action: str, provider: str, data: dict[str, Any]) -> None:
    """Log a credential event, masking sensitive values."""
    masked: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, str) and len(value) > 8:
            masked[key] = f"{value[:8]}..."
        elif isinstance(value, str):
            masked[key] = "***"
        else:
            masked[key] = str(type(value).__name__)
    logger.debug("Credential %s for %s: %s", action, provider, masked)
