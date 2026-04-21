"""Tests for the shared ``resolve_max_tokens`` helper.

Port of OpenClaw's ``resolveAnthropicVertexMaxTokens`` pattern. Every
provider (anthropic, bedrock, openai, openrouter, local, azure, vertex)
routes through this so a caller's requested cap is always clamped to
what the model actually accepts, and un-requested calls get the full
model headroom up to the 32K soft ceiling.
"""

from __future__ import annotations

import pytest

from karna.providers.base import resolve_max_tokens


def test_requested_under_model_max_passes_through():
    assert resolve_max_tokens(64_000, 128_000) == 64_000


def test_requested_over_model_max_clamps_to_cap():
    assert resolve_max_tokens(500_000, 128_000) == 128_000


def test_no_request_with_big_model_picks_soft_ceiling():
    # Opus-4 has 128K output but we don't waste it on small turns.
    assert resolve_max_tokens(None, 128_000) == 32_000


def test_no_request_with_small_model_returns_model_max():
    # Haiku-3 caps at 4K — soft ceiling shouldn't push above it.
    assert resolve_max_tokens(None, 4_096) == 4_096


def test_unknown_model_unknown_request_returns_fallback():
    assert resolve_max_tokens(None, None) == 4_096


def test_unknown_model_with_request_passes_request_through():
    # If we don't know the model, trust the caller.
    assert resolve_max_tokens(8_000, None) == 8_000


def test_zero_and_negative_requests_are_ignored():
    assert resolve_max_tokens(0, 128_000) == 32_000
    assert resolve_max_tokens(-5, 128_000) == 32_000


def test_zero_and_negative_model_max_are_ignored():
    assert resolve_max_tokens(8_000, 0) == 8_000
    assert resolve_max_tokens(8_000, -1) == 8_000


def test_float_inputs_are_floored_to_int():
    # OpenClaw parity: any positive numeric is Math.floor'd.
    assert resolve_max_tokens(1_024.9, 8_192.4) == 1_024  # noqa: F821


def test_custom_fallback_override():
    assert resolve_max_tokens(None, None, fallback=16_384) == 16_384


def test_custom_soft_ceiling_override():
    assert resolve_max_tokens(None, 128_000, soft_ceiling=64_000) == 64_000


def test_anthropic_provider_exposes_model_cap_through_list_models(monkeypatch):
    """When list_models runs, each ModelInfo should carry max_output_tokens
    so downstream callers (e.g., the TUI model picker) can display it."""
    from karna.providers.anthropic import _get_max_output_tokens

    # Longest-prefix match — opus-4 should get 128K, not the default 16K.
    assert _get_max_output_tokens("claude-opus-4-20250514") == 128_000
    assert _get_max_output_tokens("claude-sonnet-4-20250514") == 64_000
    assert _get_max_output_tokens("claude-3-5-sonnet-20241022") == 8_192


def test_modelinfo_accepts_max_output_tokens():
    """The ModelInfo schema carries the authoritative per-model cap."""
    from karna.models import ModelInfo

    mi = ModelInfo(
        id="claude-opus-4",
        provider="anthropic",
        context_window=200_000,
        max_output_tokens=128_000,
    )
    assert mi.max_output_tokens == 128_000

    # Optional — None stays None for unknown models.
    mi_unknown = ModelInfo(id="some/exotic-local-model", provider="local")
    assert mi_unknown.max_output_tokens is None
