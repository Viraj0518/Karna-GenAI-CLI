"""Cron runner — computes due jobs and executes them.

``scan_and_fire()`` is the entry point wired up to both ``nellie cron tick``
and :mod:`karna.cron.daemon`. It iterates over enabled jobs, asks
:func:`karna.cron.expression.is_due` whether they should fire, and dispatches
each due job through :func:`run_job`.

Job execution uses the non-streaming agent loop so a single ``run_job`` call
produces a deterministic ``Message`` suitable for recording in the store.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from karna.cron.expression import is_due, next_fire_time
from karna.cron.store import CronJob, CronStore

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- #
#  Executor indirection (lets tests swap in a stub)
# --------------------------------------------------------------------- #

# A job executor takes a CronJob and returns the final assistant text.
JobExecutor = Callable[[CronJob], "asyncio.Future[str] | str"]


async def _default_executor(job: CronJob) -> str:
    """Execute *job* through the real agent loop.

    We import heavy modules lazily — this function is only reached on
    actual cron ticks, never during tests (tests inject a stub executor).
    """
    from karna.agents.loop import agent_loop_sync
    from karna.config import load_config
    from karna.models import Conversation, Message
    from karna.providers import get_provider, resolve_model

    cfg = load_config()
    model_spec = job.model or f"{cfg.active_provider}:{cfg.active_model}"
    provider_name, model = resolve_model(model_spec)
    # Most providers accept a ``model=`` kwarg; fall back to a plain
    # construct if they don't (rare).
    try:
        provider = get_provider(provider_name, model=model)
    except TypeError:
        provider = get_provider(provider_name)
    conv = Conversation(
        messages=[Message(role="user", content=job.prompt)],
        model=model,
        provider=provider_name,
    )
    result = await agent_loop_sync(provider, conv, tools=[], max_iterations=10)
    return result.content or ""


def next_fire_time_for(job: CronJob, *, now: datetime | None = None) -> datetime:
    """Return the next UTC firing time for *job*, anchored on last run or now."""
    anchor = job.last_run_datetime or (now or datetime.now(timezone.utc))
    return next_fire_time(job.schedule, after=anchor)


async def run_job(
    job: CronJob,
    *,
    store: CronStore | None = None,
    executor: JobExecutor | None = None,
) -> str:
    """Execute *job* once and record the result in the store.

    Returns the (possibly empty) assistant text.
    """
    exec_fn = executor or _default_executor
    try:
        result = exec_fn(job)
        if asyncio.iscoroutine(result):
            text = await result
        else:
            text = result  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001 — cron should never crash the tick
        logger.exception("cron job %s failed: %s", job.id, exc)
        text = f"[error] {exc!s}"

    if store is not None:
        store.record_run(job.id, text)
    return text or ""


async def scan_and_fire(
    *,
    store: CronStore | None = None,
    executor: JobExecutor | None = None,
    now: datetime | None = None,
) -> list[tuple[CronJob, str]]:
    """Fire every enabled job whose schedule is due.

    Returns a list of ``(job, result_text)`` pairs for each job fired.
    """
    store = store or CronStore()
    now = now or datetime.now(timezone.utc)

    fired: list[tuple[CronJob, str]] = []
    for job in store.list_jobs():
        if not job.enabled:
            continue
        try:
            if not is_due(job.schedule, job.last_run_datetime, now=now):
                continue
        except Exception as exc:  # noqa: BLE001 — bad expressions shouldn't abort the whole scan
            logger.warning("cron job %s has invalid schedule %r: %s", job.id, job.schedule, exc)
            continue

        text = await run_job(job, store=store, executor=executor)
        fired.append((job, text))
    return fired


def summarize_job(job: CronJob) -> dict[str, Any]:
    """Return a ``dict`` suitable for pretty-printing a job."""
    try:
        nxt: str | None = next_fire_time_for(job).isoformat()
    except Exception:  # noqa: BLE001
        nxt = None
    return {
        "id": job.id,
        "name": job.name,
        "schedule": job.schedule,
        "enabled": job.enabled,
        "last_run_at": job.last_run_at,
        "next_fire_at": nxt,
        "model": job.model,
    }
