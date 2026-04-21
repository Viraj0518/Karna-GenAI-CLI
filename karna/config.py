"""Configuration management for Karna.

Loads/saves config from ``~/.karna/config.toml``.

Security:
- Config file ``~/.karna/config.toml`` is mode 0644 (readable, not secret).
- ``~/.karna/credentials/`` directory is mode 0700.
- All files IN credentials/ are mode 0600.
- Startup check warns if permissions are too open.
"""

from __future__ import annotations

import errno
import logging
import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

import tomli_w

logger = logging.getLogger(__name__)

KARNA_DIR = Path.home() / ".karna"
CONFIG_PATH = KARNA_DIR / "config.toml"


class ConfigError(RuntimeError):
    """Raised when the Karna config file cannot be loaded/parsed.

    Surfaces a clear, actionable error to the user (file path + parse
    error) rather than silently returning defaults or crashing with a
    raw tomllib traceback.
    """


class MemoryConfig(BaseModel):
    """Configuration for the persistent memory system.

    Serialised as a ``[memory]`` section in ``config.toml``.
    """

    directory: str = Field(
        default="~/.karna/memory",
        description="Root directory for memory files (supports ~ expansion)",
    )
    types: list[str] = Field(
        default=["user", "feedback", "project", "reference"],
        description="Allowed memory types (built-in + custom)",
    )
    auto_extract: bool = Field(
        default=True,
        description="Automatically extract memories from conversation turns",
    )
    rate_limit_turns: int = Field(
        default=5,
        ge=0,
        description="Minimum turns between automatic memory saves",
    )
    dedup_threshold: float = Field(
        default=0.60,
        ge=0.0,
        le=1.0,
        description="Word-overlap ratio above which a candidate is considered a duplicate",
    )
    index_file: str = Field(
        default="MEMORY.md",
        description="Name of the index file inside the memory directory",
    )


# Built-in memory types — always present regardless of config.
_BUILTIN_MEMORY_TYPES: tuple[str, ...] = ("user", "feedback", "project", "reference")


class AgentConfig(BaseModel):
    """Configuration for the agent identity.

    Serialised as an ``[agent]`` section in ``config.toml``.
    """

    name: str = Field(
        default="default",
        description="Name of this agent instance (used for inter-agent comms)",
    )


class KarnaConfig(BaseModel):
    """Top-level configuration persisted to ``~/.karna/config.toml``."""

    active_model: str = Field(
        default="openrouter/auto", description="Currently active model identifier (<provider>/<model>)"
    )
    active_provider: str = Field(default="openrouter", description="Provider name for the active model")
    system_prompt: str = Field(
        default="You are Nellie, Karna's AI assistant.",
        description="Default system prompt sent with every conversation",
    )
    max_tokens: int = Field(default=4096, ge=1, description="Max tokens for completion")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="Sampling temperature")
    safe_mode: bool = Field(default=False, description="Block dangerous bash commands instead of warning")
    # Thinking-mode toggle. ``None`` means "auto" — use the per-model smart default
    # computed by :func:`effective_thinking`. ``True`` / ``False`` override it.
    thinking_enabled: bool | None = Field(
        default=None,
        description="Thinking mode: True=on, False=off, None=auto (per-model default)",
    )
    thinking_budget_tokens: int = Field(
        default=10000,
        ge=1,
        description="Reasoning/thinking token budget requested from the provider when thinking is on",
    )
    memory: MemoryConfig = Field(
        default_factory=MemoryConfig,
        description="Persistent memory system configuration",
    )
    agent: AgentConfig = Field(
        default_factory=AgentConfig,
        description="Agent identity configuration",
    )


# Substrings that flag a model as "reasoning-capable" for auto-default purposes.
# Matched case-insensitively against the resolved model name. The spec-required
# set is ``thinking`` / ``reasoning`` / ``gpt-oss`` / ``o1`` / ``o3`` / ``kimi``
# / ``deepseek-r1``; we additionally treat the Claude 4.x family as thinking-on
# by default since every model in that family supports Anthropic's extended
# thinking parameter.
_THINKING_MODEL_HINTS: tuple[str, ...] = (
    "thinking",
    "reasoning",
    "gpt-oss",
    "o1",
    "o3",
    "kimi",
    "deepseek-r1",
    "claude-sonnet-4",
    "claude-opus-4",
    "claude-haiku-4",
)


def effective_thinking(model: str, cfg: "KarnaConfig | None" = None) -> bool:
    """Resolve the effective thinking-mode flag for *model*.

    Resolution order:
    1. If the user explicitly set ``cfg.thinking_enabled`` to True/False, use it.
    2. Otherwise, auto-detect from the model name: return True when the name
       contains any of :data:`_THINKING_MODEL_HINTS` (case-insensitive).
    3. Fall back to False.

    Passing ``cfg=None`` skips step 1 and uses the name-based auto default
    only — convenient for unit tests and call sites that don't have a config.
    """
    if cfg is not None and cfg.thinking_enabled is not None:
        return bool(cfg.thinking_enabled)
    if not model:
        return False
    lowered = model.lower()
    return any(hint in lowered for hint in _THINKING_MODEL_HINTS)


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

    Raises
    ------
    ConfigError
        If the config file exists but cannot be parsed as TOML or fails
        schema validation.  A missing file is NOT an error — we
        transparently create one with defaults.
    """
    _ensure_dir()
    if CONFIG_PATH.exists():
        try:
            raw = CONFIG_PATH.read_bytes()
            data = tomllib.loads(raw.decode())
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(
                f"Failed to parse {CONFIG_PATH}: {exc}\nFix the file or run 'nellie config reset' to restore defaults."
            ) from exc
        except OSError as exc:
            # File vanished between exists() and read_bytes() — treat as missing.
            if exc.errno == errno.ENOENT:
                cfg = KarnaConfig()
                save_config(cfg)
                _check_permissions()
                return cfg
            raise ConfigError(f"Failed to read {CONFIG_PATH}: {exc}") from exc

        try:
            cfg = KarnaConfig(**data)
        except ValidationError as exc:
            raise ConfigError(
                f"Invalid config in {CONFIG_PATH}: {exc}\n"
                f"Fix the file or run 'nellie config reset' to restore defaults."
            ) from exc
    else:
        cfg = KarnaConfig()
        save_config(cfg)

    # Run security checks on startup
    _check_permissions()

    return cfg


def save_config(cfg: KarnaConfig) -> None:
    """Persist *cfg* to ``~/.karna/config.toml``.

    Config file is set to mode 0644 (readable by all, writable by owner).

    ``None`` values are omitted from the serialized TOML because the TOML
    spec has no concept of null — we let ``KarnaConfig`` defaults re-hydrate
    the missing keys on load (semantically identical round-trip).
    """
    _ensure_dir()
    data = {k: v for k, v in cfg.model_dump().items() if v is not None}
    CONFIG_PATH.write_bytes(tomli_w.dumps(data).encode())
    os.chmod(CONFIG_PATH, 0o644)


def _check_permissions() -> None:
    """Warn if credential files or directories have overly permissive modes."""
    from karna.auth.credentials import check_credential_permissions

    warnings = check_credential_permissions()
    for w in warnings:
        logger.warning("[security] %s", w)
