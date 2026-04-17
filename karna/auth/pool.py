"""Multi-key credential pool with automatic failover.

Manages multiple API keys per provider with configurable rotation
strategies (failover, round-robin, least-used). Keys that hit rate
limits enter a timed cooldown; keys that fail authentication are
permanently removed from the pool.

Config in ``~/.karna/credentials/<provider>.token.json``::

    {
        "keys": [
            {"api_key": "sk-or-v1-primary...", "label": "personal"},
            {"api_key": "sk-or-v1-backup...", "label": "work"}
        ],
        "strategy": "round-robin",
        "rate_limit_cooldown_seconds": 60
    }

Or single key (backward compatible)::

    {"api_key": "sk-or-v1-..."}

Ported from hermes-agent credential_pool.py (MIT).
See NOTICES.md for attribution.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Selection strategies
STRATEGY_FAILOVER = "failover"
STRATEGY_ROUND_ROBIN = "round-robin"
STRATEGY_LEAST_USED = "least-used"
_VALID_STRATEGIES = {STRATEGY_FAILOVER, STRATEGY_ROUND_ROBIN, STRATEGY_LEAST_USED}

# Default cooldown after a 429 rate-limit response
DEFAULT_COOLDOWN_SECONDS = 60


@dataclass
class KeyEntry:
    """A single API key with usage metadata."""

    api_key: str
    label: str = ""
    request_count: int = 0
    error_count: int = 0
    last_used: float | None = None
    _removed: bool = field(default=False, repr=False)

    @property
    def masked_key(self) -> str:
        """Return first 8 chars + ellipsis for safe logging."""
        if len(self.api_key) > 8:
            return f"{self.api_key[:8]}..."
        return "***"


class AllKeysExhaustedError(Exception):
    """Raised when every key in the pool is in cooldown or removed."""


class CredentialPool:
    """Multi-key pool with automatic failover on rate-limit or auth errors.

    Provides key rotation via three strategies:

    * ``failover`` (default): use the first available key; only rotate
      when the current key is rate-limited or removed.
    * ``round-robin``: cycle through keys on every ``get_key()`` call.
    * ``least-used``: pick the key with the fewest ``request_count``.

    Keys that receive a 429 response are placed in a timed cooldown.
    Keys that fail authentication (401/403) are permanently removed.
    """

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.keys: list[KeyEntry] = []
        self.strategy: str = STRATEGY_FAILOVER
        self.cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
        self._current_index: int = 0
        self._cooldowns: dict[str, float] = {}  # api_key -> cooldown_expires_at

    # ------------------------------------------------------------------ #
    #  Factory
    # ------------------------------------------------------------------ #

    @classmethod
    def from_credential_data(cls, provider: str, data: dict[str, Any]) -> "CredentialPool":
        """Build a pool from the raw credential dict.

        Supports both multi-key format (``keys`` list) and single-key
        backward-compatible format (``api_key`` string).
        """
        pool = cls(provider)

        if "keys" in data and isinstance(data["keys"], list):
            # Multi-key config
            for i, entry in enumerate(data["keys"]):
                if isinstance(entry, dict) and entry.get("api_key"):
                    pool.keys.append(
                        KeyEntry(
                            api_key=entry["api_key"],
                            label=entry.get("label", f"key-{i}"),
                        )
                    )
            pool.strategy = data.get("strategy", STRATEGY_FAILOVER)
            if pool.strategy not in _VALID_STRATEGIES:
                logger.warning(
                    "Unknown strategy %r for %s, falling back to failover",
                    pool.strategy, provider,
                )
                pool.strategy = STRATEGY_FAILOVER
            pool.cooldown_seconds = float(
                data.get("rate_limit_cooldown_seconds", DEFAULT_COOLDOWN_SECONDS)
            )
        elif data.get("api_key"):
            # Single-key backward-compatible config
            pool.keys.append(
                KeyEntry(
                    api_key=data["api_key"],
                    label="default",
                )
            )

        return pool

    # ------------------------------------------------------------------ #
    #  Key selection
    # ------------------------------------------------------------------ #

    def get_key(self) -> str:
        """Get the next available key based on the configured strategy.

        Skips keys that are in cooldown (rate-limited recently) or
        permanently removed (invalid/revoked).

        Raises ``AllKeysExhaustedError`` if no key is available.
        """
        available = self._available_keys()
        if not available:
            raise AllKeysExhaustedError(
                f"All API keys for {self.provider} are exhausted "
                f"(rate-limited or auth-failed). "
                f"Active cooldowns: {len(self._cooldowns)}, "
                f"removed: {sum(1 for k in self.keys if k._removed)}"
            )

        if self.strategy == STRATEGY_ROUND_ROBIN:
            entry = self._select_round_robin(available)
        elif self.strategy == STRATEGY_LEAST_USED:
            entry = self._select_least_used(available)
        else:
            # failover: always pick the first available
            entry = available[0]

        entry.request_count += 1
        entry.last_used = time.monotonic()

        logger.debug(
            "credential pool [%s]: selected key %s (strategy=%s, count=%d)",
            self.provider, entry.masked_key, self.strategy, entry.request_count,
        )
        return entry.api_key

    def _available_keys(self) -> list[KeyEntry]:
        """Return keys not in cooldown and not removed."""
        now = time.monotonic()
        # Expire old cooldowns
        expired = [k for k, t in self._cooldowns.items() if now >= t]
        for k in expired:
            del self._cooldowns[k]
            logger.debug(
                "credential pool [%s]: cooldown expired for key",
                self.provider,
            )

        return [
            entry
            for entry in self.keys
            if not entry._removed and entry.api_key not in self._cooldowns
        ]

    def _select_round_robin(self, available: list[KeyEntry]) -> KeyEntry:
        """Select next key in round-robin order among available keys."""
        # Map available keys to their indices in self.keys
        available_indices = [
            i for i, k in enumerate(self.keys) if k in available
        ]
        # Find the next index >= _current_index
        for idx in available_indices:
            if idx >= self._current_index:
                self._current_index = idx + 1
                return self.keys[idx]
        # Wrap around
        self._current_index = available_indices[0] + 1
        return self.keys[available_indices[0]]

    def _select_least_used(self, available: list[KeyEntry]) -> KeyEntry:
        """Select the key with the fewest requests."""
        return min(available, key=lambda k: k.request_count)

    # ------------------------------------------------------------------ #
    #  Status marking
    # ------------------------------------------------------------------ #

    def mark_rate_limited(self, key: str) -> None:
        """Put *key* in cooldown after a 429 response.

        The key becomes available again after ``cooldown_seconds``.
        """
        expires_at = time.monotonic() + self.cooldown_seconds
        self._cooldowns[key] = expires_at
        entry = self._find_entry(key)
        if entry:
            entry.error_count += 1
        logger.info(
            "credential pool [%s]: key rate-limited, cooldown %.0fs",
            self.provider, self.cooldown_seconds,
        )

    def mark_auth_failed(self, key: str) -> None:
        """Permanently remove *key* from the pool (invalid/revoked).

        The key is flagged as removed and will never be selected again
        for this pool instance.
        """
        entry = self._find_entry(key)
        if entry:
            entry._removed = True
            entry.error_count += 1
        # Also remove from cooldowns if present
        self._cooldowns.pop(key, None)
        logger.warning(
            "credential pool [%s]: key permanently removed (auth failed)",
            self.provider,
        )

    # ------------------------------------------------------------------ #
    #  Stats
    # ------------------------------------------------------------------ #

    def get_stats(self) -> dict[str, Any]:
        """Return usage statistics for each key in the pool."""
        now = time.monotonic()
        key_stats = []
        for entry in self.keys:
            in_cooldown = entry.api_key in self._cooldowns
            cooldown_remaining = 0.0
            if in_cooldown:
                cooldown_remaining = max(
                    0.0, self._cooldowns[entry.api_key] - now
                )
            key_stats.append({
                "label": entry.label,
                "masked_key": entry.masked_key,
                "request_count": entry.request_count,
                "error_count": entry.error_count,
                "removed": entry._removed,
                "in_cooldown": in_cooldown,
                "cooldown_remaining_seconds": round(cooldown_remaining, 1),
            })
        return {
            "provider": self.provider,
            "strategy": self.strategy,
            "total_keys": len(self.keys),
            "available_keys": len(self._available_keys()),
            "cooldown_seconds": self.cooldown_seconds,
            "keys": key_stats,
        }

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def _find_entry(self, key: str) -> KeyEntry | None:
        """Find a KeyEntry by its api_key string."""
        for entry in self.keys:
            if entry.api_key == key:
                return entry
        return None

    @property
    def size(self) -> int:
        """Total number of keys (including removed/cooled-down)."""
        return len(self.keys)

    @property
    def has_keys(self) -> bool:
        """True if pool was populated with at least one key."""
        return len(self.keys) > 0

    @property
    def has_available(self) -> bool:
        """True if at least one key is available right now."""
        return len(self._available_keys()) > 0
