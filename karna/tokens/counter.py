"""Model-aware token counter.

Strategy:
1. If tiktoken installed -> use cl100k_base for OpenAI/Azure, o200k_base for GPT-4o+
2. Fallback -> len(text) // 4 (rough estimate, clearly labeled)

tiktoken is optional — install with ``pip install karna[tokens]``.
"""

from __future__ import annotations

import json
import logging
import warnings
from typing import Any

from karna.models import Message

logger = logging.getLogger(__name__)

_tiktoken_checked = False
_tiktoken_available = False


def _has_tiktoken() -> bool:
    """Check (once) whether tiktoken is importable."""
    global _tiktoken_checked, _tiktoken_available
    if not _tiktoken_checked:
        try:
            import tiktoken  # noqa: F401

            _tiktoken_available = True
        except ImportError:
            _tiktoken_available = False
            warnings.warn(
                "tiktoken not installed — token counts will use len//4 fallback. "
                "Install with: pip install karna[tokens]",
                stacklevel=2,
            )
        _tiktoken_checked = True
    return _tiktoken_available


# Models that should use o200k_base encoding
_O200K_PREFIXES = ("gpt-4o", "gpt-4.1", "o3", "o4")


def _encoding_for_model(model: str) -> str:
    """Return the tiktoken encoding name for *model*."""
    model_lower = model.lower()
    for prefix in _O200K_PREFIXES:
        if prefix in model_lower:
            return "o200k_base"
    # Default: cl100k_base covers GPT-4, GPT-3.5, Claude-compat, etc.
    return "cl100k_base"


class TokenCounter:
    """Model-aware token counter with encoder caching."""

    _encoders: dict[str, Any] = {}

    @classmethod
    def _get_tiktoken_encoder(cls, model: str) -> Any | None:
        """Return a cached tiktoken encoder for *model*, or None on failure.

        tiktoken requires its BPE files to be downloaded on first use.  In
        sandboxed / offline environments (CI without network, air-gapped
        deployments) that download will raise an ``OSError``, ``IOError``,
        or similar I/O-related exception.  Rather than crashing we catch
        those errors here and return ``None``, which causes ``count()`` to
        fall back to the ``len // 4`` estimator.
        """
        global _tiktoken_available

        import tiktoken

        enc_name = _encoding_for_model(model)
        if enc_name not in cls._encoders:
            try:
                cls._encoders[enc_name] = tiktoken.get_encoding(enc_name)
            except (OSError, IOError, ValueError, RuntimeError) as exc:
                logger.debug(
                    "tiktoken encoder '%s' unavailable (%s) — falling back to len//4",
                    enc_name,
                    exc,
                )
                # Mark tiktoken as unavailable so future calls skip straight
                # to the fallback without re-attempting the download.
                _tiktoken_available = False
                return None
        return cls._encoders.get(enc_name)

    @classmethod
    def count(cls, text: str, model: str = "") -> int:
        """Count tokens for the given text and model.

        Returns 0 for empty strings.  Uses tiktoken when available,
        otherwise falls back to ``len(text) // 4``.
        """
        if not text:
            return 0
        if _has_tiktoken():
            enc = cls._get_tiktoken_encoder(model)
            if enc is not None:
                return len(enc.encode(text))
        return len(text) // 4  # fallback

    @classmethod
    def count_messages(cls, messages: list[Message], model: str = "") -> int:
        """Count tokens for a full message list (includes overhead per message).

        Per-message overhead of 4 tokens accounts for role markers and
        structural framing that all chat APIs add.
        """
        total = 0
        for msg in messages:
            total += 4  # message overhead (role, delimiters)
            total += cls.count(msg.content or "", model)
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    total += cls.count(tc.name, model)
                    # arguments is a dict — serialise to count tokens
                    args_str = json.dumps(tc.arguments) if tc.arguments else ""
                    total += cls.count(args_str, model)
            if msg.tool_results:
                for tr in msg.tool_results:
                    total += cls.count(tr.content, model)
        return total


def count_tokens(text: str, model: str = "") -> int:
    """Convenience function — delegates to ``TokenCounter.count``."""
    return TokenCounter.count(text, model)
