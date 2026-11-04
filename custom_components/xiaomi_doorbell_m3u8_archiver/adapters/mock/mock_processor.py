"""Mock processor adapter."""

from __future__ import annotations

import asyncio

from ...core.domain import Artifact, Job
from ...core.lifecycle import JobContext


class MockProcessor:
    """Processor with controllable delay/failure and concurrency tracking."""

    def __init__(self, fail_process_times: int = 0, process_delay_sec: float = 0.0) -> None:
        self.fail_process_times = fail_process_times
        self.process_delay_sec = process_delay_sec
        self.process_calls = 0
        self.current_active = 0
        self.max_active = 0

    async def process(
        self, job: Job, input_artifact: Artifact, *, context: JobContext | None = None
    ) -> Artifact:
        self.process_calls += 1
        self.current_active += 1
        self.max_active = max(self.max_active, self.current_active)
        try:
            if self.process_delay_sec:
                await asyncio.sleep(self.process_delay_sec)

            if self.fail_process_times > 0:
                self.fail_process_times -= 1
                raise RuntimeError("mock process failure")

            return Artifact(
                kind="output",
                uri_or_path=f"mem://{job.job_id}/output.mp4",
                meta={"source": input_artifact.uri_or_path},
            )
        finally:
            self.current_active -= 1
