"""Tests for cost tracking (CostTracker + pricing)."""

from __future__ import annotations

from pathlib import Path

import pytest

from karna.models import Usage
from karna.sessions.cost import CostTracker, PRICING, compute_cost, _DEFAULT_PRICING
from karna.sessions.db import SessionDB


@pytest.fixture()
def db(tmp_path: Path) -> SessionDB:
    return SessionDB(db_path=tmp_path / "test.db")


@pytest.fixture()
def tracker(db: SessionDB) -> CostTracker:
    sid = db.create_session(model="gpt-4o", provider="openai", cwd="/tmp")
    return CostTracker(db=db, session_id=sid, model="gpt-4o", provider="openai")


# ------------------------------------------------------------------ #
#  Pricing lookup
# ------------------------------------------------------------------ #


def test_pricing_known_model() -> None:
    """Known models should have non-zero pricing entries."""
    assert "openai/gpt-4o" in PRICING
    assert PRICING["openai/gpt-4o"]["input"] > 0
    assert PRICING["openai/gpt-4o"]["output"] > 0


def test_pricing_anthropic_model() -> None:
    assert "anthropic/claude-opus-4-6" in PRICING
    assert PRICING["anthropic/claude-opus-4-6"]["input"] == 0.015


def test_compute_cost_known_model() -> None:
    usage = Usage(input_tokens=1000, output_tokens=500)
    cost = compute_cost("gpt-4o", "openai", usage)
    # 1000 * 0.005 / 1000 + 500 * 0.015 / 1000 = 0.005 + 0.0075 = 0.0125
    assert abs(cost - 0.0125) < 1e-6


def test_compute_cost_with_provider_prefix() -> None:
    """Should match via composite key prefix matching."""
    usage = Usage(input_tokens=1000, output_tokens=1000)
    cost = compute_cost("claude-opus-4-6", "anthropic", usage)
    # 1000 * 0.015 / 1000 + 1000 * 0.075 / 1000 = 0.015 + 0.075 = 0.09
    assert abs(cost - 0.09) < 1e-6


def test_compute_cost_precomputed() -> None:
    """If usage.cost_usd is already set, use it directly."""
    usage = Usage(input_tokens=1000, output_tokens=500, cost_usd=0.042)
    cost = compute_cost("whatever", "whatever", usage)
    assert cost == 0.042


def test_compute_cost_unknown_model() -> None:
    """Unknown model should fall back to default pricing."""
    usage = Usage(input_tokens=1000, output_tokens=1000)
    cost = compute_cost("totally-unknown-model", "unknown-provider", usage)
    expected = (
        1000 * _DEFAULT_PRICING["input"] / 1000
        + 1000 * _DEFAULT_PRICING["output"] / 1000
    )
    assert abs(cost - expected) < 1e-6


# ------------------------------------------------------------------ #
#  CostTracker accumulation
# ------------------------------------------------------------------ #


def test_record_usage_accumulates(tracker: CostTracker) -> None:
    tracker.record_usage(Usage(input_tokens=100, output_tokens=50))
    tracker.record_usage(Usage(input_tokens=200, output_tokens=100))

    summary = tracker.get_session_summary()
    assert summary["input_tokens"] == 300
    assert summary["output_tokens"] == 150
    assert summary["cost_usd"] > 0


def test_session_summary_format(tracker: CostTracker) -> None:
    tracker.record_usage(Usage(input_tokens=1000, output_tokens=500))
    summary = tracker.get_session_summary()
    assert "input_tokens" in summary
    assert "output_tokens" in summary
    assert "cost_usd" in summary
    assert isinstance(summary["cost_usd"], float)


def test_record_usage_persists_to_db(db: SessionDB, tracker: CostTracker) -> None:
    tracker.record_usage(Usage(input_tokens=100, output_tokens=50))
    session = db.get_session(tracker.session_id)
    assert session is not None
    assert session["total_input_tokens"] == 100
    assert session["total_output_tokens"] == 50
    assert session["total_cost_usd"] > 0


# ------------------------------------------------------------------ #
#  Aggregate summaries
# ------------------------------------------------------------------ #


def test_today_summary(tracker: CostTracker) -> None:
    tracker.record_usage(Usage(input_tokens=100, output_tokens=50))
    today = tracker.get_today_summary()
    assert today["input_tokens"] >= 100
    assert today["session_count"] >= 1


def test_weekly_summary(tracker: CostTracker) -> None:
    tracker.record_usage(Usage(input_tokens=100, output_tokens=50))
    weekly = tracker.get_weekly_summary()
    assert weekly["input_tokens"] >= 100


def test_total_summary(tracker: CostTracker) -> None:
    tracker.record_usage(Usage(input_tokens=100, output_tokens=50))
    total = tracker.get_total_summary()
    assert total["input_tokens"] >= 100
    assert total["session_count"] >= 1


def test_by_model_breakdown(db: SessionDB) -> None:
    sid1 = db.create_session(model="gpt-4o", provider="openai", cwd="/tmp")
    sid2 = db.create_session(model="claude", provider="anthropic", cwd="/tmp")
    db.update_session_cost(sid1, input_tokens=100, output_tokens=50, cost_usd=0.005)
    db.update_session_cost(sid2, input_tokens=200, output_tokens=100, cost_usd=0.010)

    tracker = CostTracker(db=db, session_id=sid1, model="gpt-4o", provider="openai")
    by_model = tracker.get_by_model(days=30)
    assert "gpt-4o" in by_model
    assert "claude" in by_model
    assert by_model["claude"]["cost_usd"] > by_model["gpt-4o"]["cost_usd"]
