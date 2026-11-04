"""Job repository interface."""

from __future__ import annotations

from typing import Protocol

from ..domain import Job


class JobRepositoryPort(Protocol):
    """Persistence adapter for job state."""

    async def save(self, job: Job) -> None:
        """Persist a newly created job."""

    async def get(self, job_id: str) -> Job | None:
        """Read an existing job."""

    async def update(self, job: Job) -> None:
        """Persist a job state update."""

    def metrics(self) -> dict[str, int | str]:
        """Return counters and the most recently updated job status."""
