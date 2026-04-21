"""Opt-in usage telemetry for Nellie (Goose-parity row #26).

Default is **zero telemetry** — nothing is recorded unless the user
explicitly opts in via ``nellie config set tui.telemetry_enabled true``
or ``export KARNA_TELEMETRY=1``. When enabled, events are appended
line-by-line to ``~/.karna/telemetry.jsonl`` as JSON. No network
transmission — the file is local-only; if the user wants to ship it
somewhere, they can tail/upload it themselves.

Recorded events are minimal::

    {"ts": <iso8601>, "kind": "turn_complete", "provider": "anthropic",
     "model": "claude-opus-4", "halt": "done", "input_tokens": 2145,
     "output_tokens": 186, "duration_s": 4.2, "tool_calls": 3}

    {"ts": <iso8601>, "kind": "error", "where": "agent_loop", "type": "401"}

Intentionally NOT recorded: message contents, tool arguments, tool
results, user identity, file paths the user opened. Tokens + durations
only, so the user (or the Karna team if they ship it upstream) can
measure provider cost + model performance without leaking conversations.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_TELEMETRY_PATH = Path.home() / ".karna" / "telemetry.jsonl"
_LOCK = threading.Lock()


def is_enabled() -> bool:
    """Return True only if the user has explicitly opted in.

    Two opt-in channels:
      1. ``KARNA_TELEMETRY`` env var set to ``1``/``true``/``yes``.
      2. ``[tui].telemetry_enabled = true`` in ``~/.karna/config.toml``.
    """
    env = os.environ.get("KARNA_TELEMETRY", "").lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    try:
        from karna.config import load_config

        cfg = load_config()
    except Exception:  # noqa: BLE001 — config errors should never block the agent
        return False
    tui = getattr(cfg, "tui", None) or {}
    if isinstance(tui, dict):
        return bool(tui.get("telemetry_enabled", False))
    return bool(getattr(tui, "telemetry_enabled", False))


def record(kind: str, **fields: Any) -> None:
    """Append an event to the telemetry log. No-op if opted out.

    Swallows every exception: telemetry must never bring the agent loop
    down. If the disk is full or the config is corrupt, we silently skip.
    """
    if not is_enabled():
        return
    try:
        _TELEMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kind": kind,
            **fields,
        }
        line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
        with _LOCK, _TELEMETRY_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:  # noqa: BLE001
        return


def record_turn(
    *,
    provider: str,
    model: str,
    halt: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_s: float = 0.0,
    tool_calls: int = 0,
) -> None:
    """Shorthand for the most common ``turn_complete`` event."""
    record(
        "turn_complete",
        provider=provider,
        model=model,
        halt=halt,
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        duration_s=round(float(duration_s), 3),
        tool_calls=int(tool_calls),
    )


def record_error(where: str, error_type: str, **extra: Any) -> None:
    """Shorthand for error events — capture class, not message."""
    record("error", where=where, type=error_type, **extra)


def export_summary() -> dict[str, Any]:
    """Lightweight aggregation over the local telemetry log.

    Returns a dict with ``{turns, total_input, total_output, total_cost,
    by_model: {model_id: {count, in, out}}}``. Used by a future
    ``nellie telemetry summary`` CLI command; safe to call on an empty
    or missing log (returns zeros).
    """
    if not _TELEMETRY_PATH.exists():
        return {"turns": 0, "total_input": 0, "total_output": 0, "by_model": {}}
    turns = 0
    total_in = 0
    total_out = 0
    by_model: dict[str, dict[str, int]] = {}
    try:
        for line in _TELEMETRY_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("kind") != "turn_complete":
                continue
            turns += 1
            total_in += int(ev.get("input_tokens", 0))
            total_out += int(ev.get("output_tokens", 0))
            mid = ev.get("model", "?")
            slot = by_model.setdefault(mid, {"count": 0, "in": 0, "out": 0})
            slot["count"] += 1
            slot["in"] += int(ev.get("input_tokens", 0))
            slot["out"] += int(ev.get("output_tokens", 0))
    except Exception:  # noqa: BLE001
        pass
    return {
        "turns": turns,
        "total_input": total_in,
        "total_output": total_out,
        "by_model": by_model,
    }
