"""Karna cron scheduler.

Run agent prompts on a recurring schedule. Jobs are stored at
``~/.karna/cron/jobs.toml``. Two ways to run them:

1. ``nellie cron tick`` — run all due jobs once (for OS-level cron wrapping).
2. ``karna.cron.daemon.run_daemon()`` — long-running polling loop.

Public API::

    from karna.cron import CronJob, CronStore, next_fire_time, run_job
"""

from __future__ import annotations

from karna.cron.expression import next_fire_time, parse_expression
from karna.cron.jobs import YAMLJobStore
from karna.cron.scheduler import CronScheduler
from karna.cron.store import CronJob, CronStore

__all__ = [
    "CronJob",
    "CronScheduler",
    "CronStore",
    "YAMLJobStore",
    "next_fire_time",
    "parse_expression",
]
