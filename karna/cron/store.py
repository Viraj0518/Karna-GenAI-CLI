"""Persistent store for cron jobs.

Jobs are persisted to ``~/.karna/cron/jobs.toml`` as a list of
``[[job]]`` tables. Each job has a stable 8-char hex id.
"""

from __future__ import annotations

import secrets
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found]

import tomli_w

_DEFAULT_STORE_DIR = Path.home() / ".karna" / "cron"
_DEFAULT_STORE_PATH = _DEFAULT_STORE_DIR / "jobs.toml"


@dataclass
class CronJob:
    """A scheduled agent prompt."""

    id: str
    name: str
    schedule: str
    prompt: str
    model: str = ""
    enabled: bool = True
    last_run_at: str | None = None
    last_result_snippet: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def last_run_datetime(self) -> datetime | None:
        """Parse ``last_run_at`` into a UTC datetime, or None."""
        if not self.last_run_at:
            return None
        try:
            dt = datetime.fromisoformat(self.last_run_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def to_dict(self) -> dict[str, Any]:
        """Serialize for TOML output (drops None fields)."""
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


class CronStore:
    """TOML-backed cron job store.

    Safe for concurrent reads but not concurrent writes; callers should
    ensure a single writer (the cron runner is the intended writer).
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _DEFAULT_STORE_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except (OSError, NotImplementedError):
            pass

    # ------------------------------------------------------------------ #
    #  I/O
    # ------------------------------------------------------------------ #

    def _read_raw(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"job": []}
        with self.path.open("rb") as fh:
            return tomllib.load(fh)

    def _write_raw(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("wb") as fh:
            tomli_w.dump(data, fh)
        tmp.replace(self.path)
        try:
            self.path.chmod(0o600)
        except (OSError, NotImplementedError):
            pass

    # ------------------------------------------------------------------ #
    #  CRUD
    # ------------------------------------------------------------------ #

    def list_jobs(self) -> list[CronJob]:
        raw = self._read_raw()
        jobs = raw.get("job", []) or []
        return [_job_from_dict(j) for j in jobs]

    def get_job(self, job_id: str) -> CronJob | None:
        for j in self.list_jobs():
            if j.id == job_id or j.id.startswith(job_id):
                return j
        return None

    def add_job(
        self,
        *,
        name: str,
        schedule: str,
        prompt: str,
        model: str = "",
        enabled: bool = True,
    ) -> CronJob:
        """Insert a new job and return it."""
        job = CronJob(
            id=secrets.token_hex(4),
            name=name,
            schedule=schedule,
            prompt=prompt,
            model=model,
            enabled=enabled,
        )
        raw = self._read_raw()
        jobs = list(raw.get("job", []) or [])
        jobs.append(job.to_dict())
        raw["job"] = jobs
        self._write_raw(raw)
        return job

    def remove_job(self, job_id: str) -> bool:
        raw = self._read_raw()
        jobs = list(raw.get("job", []) or [])
        before = len(jobs)
        jobs = [j for j in jobs if j.get("id") != job_id and not j.get("id", "").startswith(job_id)]
        if len(jobs) == before:
            return False
        raw["job"] = jobs
        self._write_raw(raw)
        return True

    def update_job(self, job: CronJob) -> bool:
        """Replace the job with matching id. Returns False if not found."""
        raw = self._read_raw()
        jobs = list(raw.get("job", []) or [])
        found = False
        for i, j in enumerate(jobs):
            if j.get("id") == job.id:
                jobs[i] = job.to_dict()
                found = True
                break
        if not found:
            return False
        raw["job"] = jobs
        self._write_raw(raw)
        return True

    def set_enabled(self, job_id: str, enabled: bool) -> bool:
        job = self.get_job(job_id)
        if job is None:
            return False
        job.enabled = enabled
        return self.update_job(job)

    def record_run(self, job_id: str, snippet: str | None) -> bool:
        job = self.get_job(job_id)
        if job is None:
            return False
        job.last_run_at = datetime.now(timezone.utc).isoformat()
        job.last_result_snippet = (snippet or "")[:500] or None
        updated = self.update_job(job)
        if updated:
            self._sync_yaml(job)
        return updated

    def _sync_yaml(self, job: CronJob) -> None:
        """Best-effort sync of a single job to the YAML mirror."""
        try:
            from karna.cron.jobs import YAMLJobStore

            yaml_store = YAMLJobStore(self.path.parent)
            yaml_store.save_job(job)
        except Exception:  # noqa: BLE001
            pass


def _job_from_dict(d: dict[str, Any]) -> CronJob:
    return CronJob(
        id=d.get("id", ""),
        name=d.get("name", ""),
        schedule=d.get("schedule", ""),
        prompt=d.get("prompt", ""),
        model=d.get("model", ""),
        enabled=bool(d.get("enabled", True)),
        last_run_at=d.get("last_run_at"),
        last_result_snippet=d.get("last_result_snippet"),
        created_at=d.get("created_at", datetime.now(timezone.utc).isoformat()),
    )
