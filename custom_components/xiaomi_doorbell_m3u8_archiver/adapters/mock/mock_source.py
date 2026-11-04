"""Mock source adapter."""

from __future__ import annotations

import asyncio

from ...core.domain import Artifact, Job, SourceRequest
from ...core.lifecycle import JobContext


class MockSource:
    """In-memory source for deterministic tests."""

    def __init__(self, fail_fetch_times: int = 0, fetch_delay_sec: float = 0.0) -> None:
        self.fail_fetch_times = fail_fetch_times
        self.fetch_delay_sec = fetch_delay_sec
        self.create_calls = 0
        self.fetch_calls = 0

    async def create_job(self, request: SourceRequest) -> Job:
        self.create_calls += 1
        job_id = f"job-{self.create_calls:04d}"
        return Job(job_id=job_id, source_ref=request.source_ref, metadata=request.payload.copy())

    async def fetch(self, job: Job, *, context: JobContext | None = None) -> Artifact:
        self.fetch_calls += 1
        if self.fetch_delay_sec:
            await asyncio.sleep(self.fetch_delay_sec)

        if self.fail_fetch_times > 0:
            self.fail_fetch_times -= 1
            raise RuntimeError("mock fetch failure")

        return Artifact(kind="input", uri_or_path=f"mem://{job.job_id}/input.m3u8")
