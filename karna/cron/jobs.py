"""YAML-based job persistence for cron jobs.

Jobs are stored as individual YAML files in ``~/.karna/cron/`` with one
file per job (``<job-id>.yaml``). This complements the TOML store with a
more human-friendly, per-file format that is easy to inspect and edit.

Public API::

    from karna.cron.jobs import YAMLJobStore
    store = YAMLJobStore()
    store.save_job(job)
    job = store.load_job("abc12345")
    store.delete_job("abc12345")
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from karna.cron.store import CronJob

logger = logging.getLogger(__name__)

_DEFAULT_JOBS_DIR = Path.home() / ".karna" / "cron"


class YAMLJobStore:
    """Persist :class:`CronJob` instances as individual YAML files.

    Each job is stored at ``<jobs_dir>/<id>.yaml``. This format is
    intended for human inspection — the authoritative store is still the
    TOML-backed :class:`~karna.cron.store.CronStore`.
    """

    def __init__(self, jobs_dir: Path | None = None) -> None:
        self.jobs_dir = jobs_dir or _DEFAULT_JOBS_DIR
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.jobs_dir.chmod(0o700)
        except (OSError, NotImplementedError):
            pass

    # ------------------------------------------------------------------ #
    #  Single-job I/O
    # ------------------------------------------------------------------ #

    def _path_for(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.yaml"

    def save_job(self, job: CronJob) -> Path:
        """Serialize *job* to a YAML file and return the path."""
        data: dict[str, Any] = {
            "id": job.id,
            "name": job.name,
            "schedule": job.schedule,
            "prompt": job.prompt,
            "skill": job.model or None,
            "enabled": job.enabled,
            "created_at": job.created_at,
            "last_run": job.last_run_at,
        }
        path = self._path_for(job.id)
        with path.open("w") as fh:
            yaml.dump(data, fh, default_flow_style=False, sort_keys=False)
        try:
            path.chmod(0o600)
        except (OSError, NotImplementedError):
            pass
        return path

    def load_job(self, job_id: str) -> CronJob | None:
        """Load a single job by id. Returns ``None`` if the file is missing."""
        path = self._path_for(job_id)
        if not path.exists():
            return None
        try:
            with path.open() as fh:
                data = yaml.safe_load(fh) or {}
        except Exception:
            logger.warning("failed to load job YAML: %s", path)
            return None
        return _job_from_yaml(data)

    def delete_job(self, job_id: str) -> bool:
        """Delete the YAML file for *job_id*. Returns ``False`` if not found."""
        path = self._path_for(job_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    # ------------------------------------------------------------------ #
    #  Bulk operations
    # ------------------------------------------------------------------ #

    def list_jobs(self) -> list[CronJob]:
        """Load all ``*.yaml`` job files from the jobs directory."""
        jobs: list[CronJob] = []
        for path in sorted(self.jobs_dir.glob("*.yaml")):
            try:
                with path.open() as fh:
                    data = yaml.safe_load(fh) or {}
                jobs.append(_job_from_yaml(data))
            except Exception:
                logger.warning("skipping unreadable job file: %s", path)
        return jobs

    def sync_from_store(self, jobs: list[CronJob]) -> None:
        """Write a YAML file for each job (idempotent sync)."""
        for job in jobs:
            self.save_job(job)


def _job_from_yaml(data: dict[str, Any]) -> CronJob:
    """Construct a :class:`CronJob` from a parsed YAML mapping."""
    return CronJob(
        id=str(data.get("id", "")),
        name=str(data.get("name", "")),
        schedule=str(data.get("schedule", "")),
        prompt=str(data.get("prompt", "")),
        model=str(data.get("skill") or data.get("model") or ""),
        enabled=bool(data.get("enabled", True)),
        last_run_at=data.get("last_run") or data.get("last_run_at"),
        created_at=data.get("created_at") or datetime.now(timezone.utc).isoformat(),
    )
