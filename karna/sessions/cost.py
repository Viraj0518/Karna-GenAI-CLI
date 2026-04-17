"""Cost tracking across sessions.

PRIVACY: All session data stored locally at ~/.karna/sessions/sessions.db
No session data is ever sent to any external service.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from karna.models import Usage, estimate_cost
from karna.sessions.db import SessionDB

# --------------------------------------------------------------------------- #
#  Per-1K-token pricing for OpenRouter-style composite model IDs.
#  Falls back to models.estimate_cost for canonical provider/model pairs.
# --------------------------------------------------------------------------- #

PRICING: dict[str, dict[str, float]] = {
    "minimax/minimax-m2.7": {"input": 0.0003, "output": 0.0003},
    "openai/gpt-4o": {"input": 0.005, "output": 0.015},
    "openai/gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "openai/gpt-4.1": {"input": 0.002, "output": 0.008},
    "openai/gpt-4.1-mini": {"input": 0.0004, "output": 0.0016},
    "openai/gpt-4.1-nano": {"input": 0.0001, "output": 0.0004},
    "openai/o3": {"input": 0.010, "output": 0.040},
    "openai/o3-mini": {"input": 0.0011, "output": 0.0044},
    "anthropic/claude-sonnet-4-6": {"input": 0.003, "output": 0.015},
    "anthropic/claude-opus-4-6": {"input": 0.015, "output": 0.075},
    "anthropic/claude-3-5-sonnet": {"input": 0.003, "output": 0.015},
    "anthropic/claude-3-5-haiku": {"input": 0.0008, "output": 0.004},
    "google/gemini-2.0-flash": {"input": 0.0001, "output": 0.0004},
    "google/gemini-2.5-pro": {"input": 0.00125, "output": 0.01},
    "meta-llama/llama-3.3-70b-instruct": {"input": 0.0003, "output": 0.0004},
    "deepseek/deepseek-chat-v3": {"input": 0.00014, "output": 0.00028},
}

# Default estimate when model is unknown — conservative mid-range
_DEFAULT_PRICING = {"input": 0.002, "output": 0.008}


def compute_cost(model: str, provider: str, usage: Usage) -> float:
    """Compute USD cost for a Usage object.

    Checks the local PRICING table first (per-1K-token), then falls back
    to ``models.estimate_cost`` (per-1M-token), then defaults to a
    conservative estimate.
    """
    # Already computed by the provider SDK
    if usage.cost_usd is not None:
        return usage.cost_usd

    # Try composite key (provider/model)
    composite = f"{provider}/{model}"
    for key, pricing in PRICING.items():
        if key in composite:
            return (
                usage.input_tokens * pricing["input"] / 1000
                + usage.output_tokens * pricing["output"] / 1000
            )

    # Fall back to models.py estimate_cost (per-1M tokens)
    est = estimate_cost(provider, model, usage.input_tokens, usage.output_tokens)
    if est is not None:
        return est

    # Unknown model — use conservative default
    return (
        usage.input_tokens * _DEFAULT_PRICING["input"] / 1000
        + usage.output_tokens * _DEFAULT_PRICING["output"] / 1000
    )


class CostTracker:
    """Accumulates token usage and cost for the current session.

    Persists each update to the session database so totals survive crashes.
    """

    def __init__(self, db: SessionDB, session_id: str, model: str = "", provider: str = "") -> None:
        self.db = db
        self.session_id = session_id
        self.model = model
        self.provider = provider

        # Running totals for the current session
        self.session_input_tokens: int = 0
        self.session_output_tokens: int = 0
        self.session_cost: float = 0.0

    def record_usage(self, usage: Usage) -> float:
        """Record tokens + cost for a single turn.

        Returns the computed cost for this turn.
        """
        cost = compute_cost(self.model, self.provider, usage)

        self.session_input_tokens += usage.input_tokens
        self.session_output_tokens += usage.output_tokens
        self.session_cost += cost

        # Persist to DB
        self.db.update_session_cost(
            self.session_id,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=cost,
        )
        return cost

    def get_session_summary(self) -> dict[str, Any]:
        """Return running totals for the current session."""
        return {
            "input_tokens": self.session_input_tokens,
            "output_tokens": self.session_output_tokens,
            "cost_usd": round(self.session_cost, 6),
        }

    # ------------------------------------------------------------------ #
    #  Aggregate queries (delegate to DB)
    # ------------------------------------------------------------------ #

    def get_today_summary(self) -> dict[str, Any]:
        """Aggregate cost for today across all sessions."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
        return self.db.get_cost_since(today)

    def get_weekly_summary(self) -> dict[str, Any]:
        """Aggregate cost for the last 7 days."""
        from datetime import timedelta

        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        return self.db.get_cost_since(week_ago)

    def get_total_summary(self) -> dict[str, Any]:
        """Aggregate cost across all sessions ever."""
        return self.db.get_total_cost()

    def get_by_model(self, days: int = 30) -> dict[str, dict[str, Any]]:
        """Cost breakdown by model over the last N days."""
        return self.db.get_cost_by_model(days)
