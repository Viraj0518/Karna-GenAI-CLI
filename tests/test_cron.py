"""Tests for the cron scheduler.

Covers:
- Schedule parsing (shortcuts + 5-field)
- Store roundtrip (add / list / update / remove)
- ``next_fire_time`` correctness on a concrete anchor
- ``is_due`` + ``scan_and_fire`` execution with a stub executor
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from karna.cron.expression import (
    CronParseError,
    is_due,
    next_fire_time,
    parse_expression,
)
from karna.cron.runner import scan_and_fire
from karna.cron.store import CronJob, CronStore

# --------------------------------------------------------------------------- #
#  Expression parsing
# --------------------------------------------------------------------------- #


def test_parse_shortcuts() -> None:
    for shortcut in ("@hourly", "@daily", "@weekly", "@monthly", "@yearly"):
        spec = parse_expression(shortcut)
        assert spec.raw == shortcut


def test_parse_five_field() -> None:
    spec = parse_expression("0 9 * * MON-FRI")
    assert spec.minutes == {0}
    assert spec.hours == {9}
    # MON-FRI = cron dow 1..5
    assert spec.dows == {1, 2, 3, 4, 5}


def test_parse_step_and_range() -> None:
    spec = parse_expression("*/15 0-6 * * *")
    assert spec.minutes == {0, 15, 30, 45}
    assert spec.hours == {0, 1, 2, 3, 4, 5, 6}


def test_parse_list() -> None:
    spec = parse_expression("0 9,17 * * *")
    assert spec.hours == {9, 17}


def test_parse_invalid_raises() -> None:
    with pytest.raises(CronParseError):
        parse_expression("")
    with pytest.raises(CronParseError):
        parse_expression("@unknown")
    with pytest.raises(CronParseError):
        parse_expression("0 9 * *")  # too few fields
    with pytest.raises(CronParseError):
        parse_expression("99 * * * *")  # out of range


# --------------------------------------------------------------------------- #
#  next_fire_time
# --------------------------------------------------------------------------- #


def test_next_fire_time_daily() -> None:
    # Monday 2026-04-20 08:00 UTC -> @daily fires next at 2026-04-21 00:00
    anchor = datetime(2026, 4, 20, 8, 0, tzinfo=timezone.utc)
    nxt = next_fire_time("@daily", after=anchor)
    assert nxt.year == 2026 and nxt.month == 4 and nxt.day == 21
    assert nxt.hour == 0 and nxt.minute == 0


def test_next_fire_time_weekday_morning() -> None:
    # Fri 2026-04-17 10:00 UTC, schedule = 9am Mon-Fri -> next is Mon 2026-04-20 09:00
    anchor = datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc)
    nxt = next_fire_time("0 9 * * MON-FRI", after=anchor)
    assert nxt.weekday() == 0  # Monday (Python's weekday)
    assert nxt.hour == 9 and nxt.minute == 0


# --------------------------------------------------------------------------- #
#  Store roundtrip
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store(tmp_path: Path) -> CronStore:
    return CronStore(path=tmp_path / "jobs.toml")


def test_store_roundtrip(store: CronStore) -> None:
    job = store.add_job(name="morning", schedule="@daily", prompt="summarize")
    assert job.id and len(job.id) == 8
    jobs = store.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].name == "morning"

    # Prefix lookup
    by_prefix = store.get_job(job.id[:4])
    assert by_prefix is not None and by_prefix.id == job.id

    assert store.set_enabled(job.id, False) is True
    assert store.get_job(job.id).enabled is False  # type: ignore[union-attr]

    assert store.record_run(job.id, "hello world") is True
    reloaded = store.get_job(job.id)
    assert reloaded is not None and reloaded.last_run_at is not None
    assert reloaded.last_result_snippet == "hello world"

    assert store.remove_job(job.id) is True
    assert store.list_jobs() == []


def test_store_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "jobs.toml"
    CronStore(path=path).add_job(name="a", schedule="@hourly", prompt="p")
    second = CronStore(path=path)
    jobs = second.list_jobs()
    assert len(jobs) == 1 and jobs[0].name == "a"


# --------------------------------------------------------------------------- #
#  is_due + scan_and_fire
# --------------------------------------------------------------------------- #


def test_is_due_first_run_is_true() -> None:
    assert is_due("@hourly", last_run_at=None) is True


def test_is_due_respects_last_run() -> None:
    now = datetime(2026, 4, 17, 10, 30, tzinfo=timezone.utc)
    # Ran last minute -> next fire for @hourly (anchor 10:29) is 11:00, not due.
    assert is_due("@hourly", last_run_at=now - timedelta(minutes=1), now=now) is False
    # Ran 2 hours ago -> anchor 08:30, next fire 09:00 <= now 10:30 -> due.
    assert is_due("@hourly", last_run_at=now - timedelta(hours=2), now=now) is True


@pytest.mark.asyncio
async def test_scan_and_fire_runs_due_jobs(store: CronStore) -> None:
    store.add_job(name="a", schedule="@hourly", prompt="do a")
    job_b = store.add_job(name="b", schedule="@hourly", prompt="do b")
    store.set_enabled(job_b.id, False)

    calls: list[str] = []

    async def fake_executor(job: CronJob) -> str:
        calls.append(job.name)
        return f"result for {job.name}"

    fired = await scan_and_fire(store=store, executor=fake_executor)
    assert [j.name for j, _ in fired] == ["a"]
    assert calls == ["a"]

    # Second scan — last_run was just recorded, so 'a' should no longer be due
    fired2 = await scan_and_fire(store=store, executor=fake_executor)
    assert fired2 == []


@pytest.mark.asyncio
async def test_scan_and_fire_swallows_bad_schedule(store: CronStore) -> None:
    job = store.add_job(name="bad", schedule="nonsense", prompt="p")
    # Set a prior run so is_due actually parses the schedule (the None
    # branch short-circuits to True for first-run).
    store.record_run(job.id, "")
    fired = await scan_and_fire(store=store, executor=lambda j: "x")
    assert fired == []  # bad schedule skipped, not raised
