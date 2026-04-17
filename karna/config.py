"""Configuration management for Karna.

Loads/saves config from ``~/.karna/config.toml``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

import tomli_w

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


def _ensure_dir() -> None:
    """Create ``~/.karna/`` and sub-dirs on first use."""
    KARNA_DIR.mkdir(parents=True, exist_ok=True)
    (KARNA_DIR / "credentials").mkdir(exist_ok=True)


def load_config() -> KarnaConfig:
    """Load config from disk, returning defaults if the file doesn't exist."""
    _ensure_dir()
    if CONFIG_PATH.exists():
        raw = CONFIG_PATH.read_bytes()
        data = tomllib.loads(raw.decode())
        return KarnaConfig(**data)
    cfg = KarnaConfig()
    save_config(cfg)
    return cfg


def save_config(cfg: KarnaConfig) -> None:
    """Persist *cfg* to ``~/.karna/config.toml``."""
    _ensure_dir()
    CONFIG_PATH.write_bytes(tomli_w.dumps(cfg.model_dump()).encode())
