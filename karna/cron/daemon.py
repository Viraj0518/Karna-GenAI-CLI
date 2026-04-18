"""Optional long-running cron daemon.

This is a convenience wrapper for users who want to keep a polling
process alive instead of invoking ``nellie cron tick`` from the OS
scheduler. The preferred deployment is still OS cron -> ``nellie cron
tick`` (less code, survives reboots for free) — this daemon exists for
environments where that isn't available (e.g., a locked-down container).

Usage::

    python -m karna.cron.daemon

or programmatically::

    asyncio.run(run_daemon(poll_seconds=30))
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from karna.cron.runner import JobExecutor, scan_and_fire
from karna.cron.store import CronStore

logger = logging.getLogger(__name__)


async def run_daemon(
    *,
    poll_seconds: int = 30,
    store: CronStore | None = None,
    executor: JobExecutor | None = None,
    stop_event: asyncio.Event | None = None,
    tick_callback: Callable[[int], None] | None = None,
) -> None:
    """Poll for due jobs every *poll_seconds* until *stop_event* is set.

    ``tick_callback`` is invoked with the count of jobs fired on each
    tick — handy for tests and progress reporting.
    """
    store = store or CronStore()
    stop_event = stop_event or asyncio.Event()

    logger.info("cron daemon started (poll=%ds)", poll_seconds)
    try:
        while not stop_event.is_set():
            try:
                fired = await scan_and_fire(store=store, executor=executor)
            except Exception:  # noqa: BLE001 — daemon must never die silently
                logger.exception("cron scan failed")
                fired = []
            if tick_callback is not None:
                tick_callback(len(fired))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                continue
    finally:
        logger.info("cron daemon stopped")


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run_daemon())
