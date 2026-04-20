"""Unified cron scheduler facade.

Provides the :class:`CronScheduler` class that wraps the underlying
:class:`~karna.cron.store.CronStore` (persistence) and
:mod:`~karna.cron.runner` (execution) into a single high-level API
suitable for both the CLI and TUI slash commands.

Public API::

    from karna.cron.scheduler import CronScheduler

    sched = CronScheduler()
    job = sched.add_job("daily-rfp", "0 6 * * 1-5", "Check SAM.gov for new RFPs")
    await sched.run_due_jobs()
    await sched.start_loop()  # blocking background loop
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from karna.cron.expression import is_due, next_fire_time, parse_expression
from karna.cron.jobs import YAMLJobStore
from karna.cron.runner import JobExecutor, run_job, scan_and_fire
from karna.cron.store import CronJob, CronStore

logger = logging.getLogger(__name__)

_DEFAULT_JOBS_DIR = Path.home() / ".karna" / "cron"


class CronScheduler:
    """High-level scheduler that ties together storage, execution, and the daemon loop.

    Parameters
    ----------
    jobs_dir:
        Root directory for cron data. Defaults to ``~/.karna/cron/``.
    """

    def __init__(self, jobs_dir: Path | None = None) -> None:
        self._jobs_dir = jobs_dir or _DEFAULT_JOBS_DIR
        # TOML store path lives inside the jobs dir.
        store_path = self._jobs_dir / "jobs.toml"
        self.store = CronStore(path=store_path)
        self.yaml_store = YAMLJobStore(jobs_dir=self._jobs_dir)

    # ------------------------------------------------------------------ #
    #  Job CRUD
    # ------------------------------------------------------------------ #

    def add_job(
        self,
        name: str,
        schedule: str,
        prompt: str,
        skill: str | None = None,
        *,
        enabled: bool = True,
    ) -> CronJob:
        """Create and persist a new cron job.

        Raises :class:`~karna.cron.expression.CronParseError` if *schedule*
        is not a valid cron expression.
        """
        parse_expression(schedule)  # validate early
        job = self.store.add_job(
            name=name,
            schedule=schedule,
            prompt=prompt,
            model=skill or "",
            enabled=enabled,
        )
        # Mirror to YAML for human inspection.
        self.yaml_store.save_job(job)
        return job

    def remove_job(self, job_id: str) -> bool:
        """Delete a job by id (or prefix). Returns ``False`` if not found."""
        job = self.store.get_job(job_id)
        if job is None:
            return False
        removed = self.store.remove_job(job.id)
        if removed:
            self.yaml_store.delete_job(job.id)
        return removed

    def list_jobs(self) -> list[CronJob]:
        """Return all configured jobs."""
        return self.store.list_jobs()

    def get_job(self, job_id: str) -> CronJob | None:
        """Look up a job by exact id or prefix."""
        return self.store.get_job(job_id)

    def enable_job(self, job_id: str) -> bool:
        """Enable a job. Returns ``False`` if the job was not found."""
        ok = self.store.set_enabled(job_id, True)
        if ok:
            job = self.store.get_job(job_id)
            if job:
                self.yaml_store.save_job(job)
        return ok

    def disable_job(self, job_id: str) -> bool:
        """Disable a job. Returns ``False`` if the job was not found."""
        ok = self.store.set_enabled(job_id, False)
        if ok:
            job = self.store.get_job(job_id)
            if job:
                self.yaml_store.save_job(job)
        return ok

    # ------------------------------------------------------------------ #
    #  Execution
    # ------------------------------------------------------------------ #

    async def run_job(self, job_id: str, *, executor: JobExecutor | None = None) -> str:
        """Execute a single job immediately by id.

        Returns the assistant response text. Raises ``KeyError`` if the
        job is not found.
        """
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(f"no cron job matches {job_id!r}")
        text = await run_job(job, store=self.store, executor=executor)
        # Sync YAML after recording the run.
        updated = self.store.get_job(job.id)
        if updated:
            self.yaml_store.save_job(updated)
        return text

    async def run_due_jobs(self, *, executor: JobExecutor | None = None) -> list[tuple[CronJob, str]]:
        """Check and execute all due jobs.

        Returns a list of ``(job, result_text)`` pairs.
        """
        fired = await scan_and_fire(store=self.store, executor=executor)
        # Sync YAML for every job that fired.
        for job, _ in fired:
            updated = self.store.get_job(job.id)
            if updated:
                self.yaml_store.save_job(updated)
        return fired

    async def start_loop(
        self,
        *,
        poll_seconds: int = 60,
        executor: JobExecutor | None = None,
        stop_event: asyncio.Event | None = None,
        tick_callback: Callable[[int], None] | None = None,
    ) -> None:
        """Run a background loop that checks for due jobs every *poll_seconds*.

        This is a convenience wrapper around :func:`karna.cron.daemon.run_daemon`
        that uses this scheduler's store instance.
        """
        from karna.cron.daemon import run_daemon

        await run_daemon(
            poll_seconds=poll_seconds,
            store=self.store,
            executor=executor,
            stop_event=stop_event,
            tick_callback=tick_callback,
        )

    # ------------------------------------------------------------------ #
    #  Introspection helpers
    # ------------------------------------------------------------------ #

    def next_run(self, job_id: str) -> datetime | None:
        """Return the next fire time for *job_id*, or ``None``."""
        job = self.store.get_job(job_id)
        if job is None:
            return None
        try:
            anchor = job.last_run_datetime or datetime.now(timezone.utc)
            return next_fire_time(job.schedule, after=anchor)
        except Exception:
            return None

    def is_due(self, job_id: str) -> bool:
        """Return ``True`` if *job_id* should fire right now."""
        job = self.store.get_job(job_id)
        if job is None:
            return False
        try:
            return is_due(job.schedule, job.last_run_datetime)
        except Exception:
            return False
