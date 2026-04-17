"""Credential storage for provider API keys.

Credentials are stored as JSON files in ``~/.karna/credentials/``
with mode 0600 for basic filesystem protection.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CREDENTIALS_DIR = Path.home() / ".karna" / "credentials"


def _ensure_dir() -> None:
    """Create the credentials directory if it doesn't exist."""
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)


def save_credential(provider: str, data: dict[str, Any]) -> Path:
    """Write *data* to ``~/.karna/credentials/<provider>.token.json``.

    The file is created with mode 0600 (owner read/write only).
    Returns the path to the written file.
    """
    _ensure_dir()
    path = CREDENTIALS_DIR / f"{provider}.token.json"
    path.write_text(json.dumps(data, indent=2))
    os.chmod(path, 0o600)
    return path


def load_credential(provider: str) -> dict[str, Any]:
    """Read and return the credential dict for *provider*.

    Returns an empty dict if no credential file exists.
    """
    path = CREDENTIALS_DIR / f"{provider}.token.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def list_credentials() -> list[str]:
    """Return a list of provider names that have saved credentials."""
    _ensure_dir()
    return [
        p.stem.removesuffix(".token")
        for p in CREDENTIALS_DIR.glob("*.token.json")
        if p.is_file()
    ]
