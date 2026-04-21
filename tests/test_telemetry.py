"""Tests for the opt-in telemetry module (Goose-parity row #26)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture()
def isolated_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point telemetry at a tmp file and clear any existing opt-in state."""
    from karna import telemetry

    log = tmp_path / "telemetry.jsonl"
    monkeypatch.setattr(telemetry, "_TELEMETRY_PATH", log)
    monkeypatch.delenv("KARNA_TELEMETRY", raising=False)
    return log


def test_opt_out_by_default(isolated_log: Path) -> None:
    from karna import telemetry

    # No env var, no config → disabled.
    telemetry.record("turn_complete", provider="anthropic", model="x")
    assert not isolated_log.exists()


def test_env_var_enables(isolated_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from karna import telemetry

    monkeypatch.setenv("KARNA_TELEMETRY", "1")
    telemetry.record("turn_complete", provider="anthropic", model="x")
    assert isolated_log.exists()
    ev = json.loads(isolated_log.read_text().strip())
    assert ev["kind"] == "turn_complete"
    assert ev["provider"] == "anthropic"
    assert "ts" in ev


def test_env_var_off_values_disable(isolated_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from karna import telemetry

    for val in ("0", "false", "no", "off"):
        monkeypatch.setenv("KARNA_TELEMETRY", val)
        assert telemetry.is_enabled() is False


def test_env_var_on_values_enable(monkeypatch: pytest.MonkeyPatch) -> None:
    from karna import telemetry

    for val in ("1", "true", "yes", "on"):
        monkeypatch.setenv("KARNA_TELEMETRY", val)
        assert telemetry.is_enabled() is True


def test_record_turn_shorthand(isolated_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from karna import telemetry

    monkeypatch.setenv("KARNA_TELEMETRY", "1")
    telemetry.record_turn(
        provider="openrouter",
        model="anthropic/claude-opus-4",
        halt="done",
        input_tokens=2145,
        output_tokens=186,
        duration_s=4.2,
        tool_calls=3,
    )
    ev = json.loads(isolated_log.read_text().strip())
    assert ev["input_tokens"] == 2145
    assert ev["output_tokens"] == 186
    assert ev["tool_calls"] == 3
    assert ev["duration_s"] == 4.2


def test_record_error_shorthand(isolated_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from karna import telemetry

    monkeypatch.setenv("KARNA_TELEMETRY", "1")
    telemetry.record_error("agent_loop", "401")
    ev = json.loads(isolated_log.read_text().strip())
    assert ev["kind"] == "error"
    assert ev["where"] == "agent_loop"
    assert ev["type"] == "401"


def test_record_swallows_disk_errors(
    isolated_log: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disk-full / permission error in telemetry must never crash the agent."""
    from karna import telemetry

    monkeypatch.setenv("KARNA_TELEMETRY", "1")

    def raise_oserror(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", raise_oserror)
    # Should not raise
    telemetry.record("turn_complete", provider="x", model="y")


def test_export_summary_on_empty_log() -> None:
    from karna import telemetry

    # Point at a path that definitely doesn't exist.
    telemetry._TELEMETRY_PATH = Path("/tmp/definitely-does-not-exist-xyz.jsonl")
    summary = telemetry.export_summary()
    assert summary == {"turns": 0, "total_input": 0, "total_output": 0, "by_model": {}}


def test_export_summary_aggregates(isolated_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from karna import telemetry

    monkeypatch.setenv("KARNA_TELEMETRY", "1")
    telemetry.record_turn(provider="a", model="opus", halt="done", input_tokens=100, output_tokens=50)
    telemetry.record_turn(provider="a", model="opus", halt="done", input_tokens=200, output_tokens=80)
    telemetry.record_turn(provider="a", model="haiku", halt="done", input_tokens=40, output_tokens=20)
    telemetry.record_error("agent_loop", "429")  # Should NOT count toward turns
    summary = telemetry.export_summary()
    assert summary["turns"] == 3
    assert summary["total_input"] == 340
    assert summary["total_output"] == 150
    assert summary["by_model"]["opus"]["count"] == 2
    assert summary["by_model"]["opus"]["in"] == 300
    assert summary["by_model"]["haiku"]["count"] == 1
