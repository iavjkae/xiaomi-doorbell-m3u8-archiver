"""In-memory runtime job repository."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone, tzinfo

from ...core.domain import Job, JobStatus


class RuntimeRepository:
    """In-memory repository with lightweight metrics updates."""

    def __init__(self, *, history_limit: int = 256, local_timezone: tzinfo = timezone.utc) -> None:
        if history_limit < 0:
            raise ValueError("history_limit must not be negative")
        self._history_limit = history_limit
        self._local_timezone = local_timezone
        self._completed_ids: deque[str] = deque()
        self._jobs: dict[str, Job] = {}
        self._status_snapshot: dict[str, JobStatus] = {}
        self.total_done = 0
        self.total_failed = 0
        self._processed_today = 0
        self._processed_day_key = self._day_key()
        self.last_job_status = JobStatus.CREATED.value

    async def save(self, job: Job) -> None:
        self._jobs[job.job_id] = job
        self._status_snapshot[job.job_id] = job.status
        self.last_job_status = job.status.value

    async def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    async def update(self, job: Job) -> None:
        self._rollover_processed_today_if_needed()
        old_status = self._status_snapshot.get(job.job_id)
        self._jobs[job.job_id] = job
        self._status_snapshot[job.job_id] = job.status
        self.last_job_status = job.status.value
        if old_status != JobStatus.DONE and job.status == JobStatus.DONE:
            self.total_done += 1
            self._processed_today += 1
        if old_status != JobStatus.FAILED and job.status == JobStatus.FAILED:
            self.total_failed += 1
        terminal = {JobStatus.DONE, JobStatus.FAILED}
        if old_status not in terminal and job.status in terminal:
            self._completed_ids.append(job.job_id)
            while len(self._completed_ids) > self._history_limit:
                expired_id = self._completed_ids.popleft()
                self._jobs.pop(expired_id, None)
                self._status_snapshot.pop(expired_id, None)

    @property
    def processed_today(self) -> int:
        """Reset the daily counter on reads even when no new jobs arrive."""
        self._rollover_processed_today_if_needed()
        return self._processed_today

    def metrics(self) -> dict[str, int | str]:
        """Keep cumulative counts independent of retained job history."""
        return {
            "total_done": self.total_done,
            "total_failed": self.total_failed,
            "processed_today": self.processed_today,
            "last_job_status": self.last_job_status,
        }

    def _day_key(self) -> str:
        return datetime.now(self._local_timezone).strftime("%Y-%m-%d")

    def _rollover_processed_today_if_needed(self) -> None:
        current = self._day_key()
        if current != self._processed_day_key:
            self._processed_day_key = current
            self._processed_today = 0
