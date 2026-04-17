"""Configuration management for Karna.

Loads/saves config from ``~/.karna/config.toml``.

Security:
- Config file ``~/.karna/config.toml`` is mode 0644 (readable, not secret).
- ``~/.karna/credentials/`` directory is mode 0700.
- All files IN credentials/ are mode 0600.
- Startup check warns if permissions are too open.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

import tomli_w

logger = logging.getLogger(__name__)

KARNA_DIR = Path.home() / ".karna"
CONFIG_PATH = KARNA_DIR / "config.toml"


class KarnaConfig(BaseModel):
    """Top-level configuration persisted to ``~/.karna/config.toml``."""

    active_model: str = Field(default="openrouter/auto", description="Currently active model identifier (<provider>/<model>)")
    active_provider: str = Field(default="openrouter", description="Provider name for the active model")
    system_prompt: str = Field(
        default="You are Karna, a helpful AI assistant.",
        description="Default system prompt sent with every conversation",
    )
    max_tokens: int = Field(default=4096, ge=1, description="Max tokens for completion")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="Sampling temperature")
    safe_mode: bool = Field(default=False, description="Block dangerous bash commands instead of warning")


def _ensure_dir() -> None:
    """Create ``~/.karna/`` and sub-dirs on first use.

    Sets credentials directory to mode 0700.
    """
    KARNA_DIR.mkdir(parents=True, exist_ok=True)
    creds_dir = KARNA_DIR / "credentials"
    creds_dir.mkdir(exist_ok=True)
    os.chmod(creds_dir, 0o700)


def load_config() -> KarnaConfig:
    """Load config from disk, returning defaults if the file doesn't exist.

    Also runs permission checks and emits warnings if anything is too open.
    """
    _ensure_dir()
    if CONFIG_PATH.exists():
        raw = CONFIG_PATH.read_bytes()
        data = tomllib.loads(raw.decode())
        cfg = KarnaConfig(**data)
    else:
        cfg = KarnaConfig()
        save_config(cfg)

    # Run security checks on startup
    _check_permissions()

    return cfg


def save_config(cfg: KarnaConfig) -> None:
    """Persist *cfg* to ``~/.karna/config.toml``.

    Config file is set to mode 0644 (readable by all, writable by owner).
    """
    _ensure_dir()
    CONFIG_PATH.write_bytes(tomli_w.dumps(cfg.model_dump()).encode())
    os.chmod(CONFIG_PATH, 0o644)


def _check_permissions() -> None:
    """Warn if credential files or directories have overly permissive modes."""
    from karna.auth.credentials import check_credential_permissions

    warnings = check_credential_permissions()
    for w in warnings:
        logger.warning("[security] %s", w)
