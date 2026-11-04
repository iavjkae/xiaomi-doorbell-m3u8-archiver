"""Mock job repository adapter."""

from __future__ import annotations

from ...core.domain import Job, JobStatus


class MockRepository:
    """Keep all test jobs in memory, treating each instance as a single day."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._last_job_status = JobStatus.CREATED.value

    async def save(self, job: Job) -> None:
        self._jobs[job.job_id] = job
        self._last_job_status = job.status.value

    async def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    async def update(self, job: Job) -> None:
        await self.save(job)

    def metrics(self) -> dict[str, int | str]:
        total_done = sum(job.status == JobStatus.DONE for job in self._jobs.values())
        return {
            "total_done": total_done,
            "total_failed": sum(job.status == JobStatus.FAILED for job in self._jobs.values()),
            "processed_today": total_done,
            "last_job_status": self._last_job_status,
        }
