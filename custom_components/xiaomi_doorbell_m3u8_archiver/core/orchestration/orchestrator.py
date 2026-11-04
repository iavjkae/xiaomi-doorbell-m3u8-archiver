"""Asynchronous pipeline orchestrator."""

from __future__ import annotations

import asyncio
import logging

from ..domain import Job, JobStatus, SourceRequest
from ..lifecycle import JobContext
from ..policies import QueuePolicy, RetentionPolicy
from ..ports import JobRepositoryPort, ProcessorPort, SourcePort, StoragePort

_LOGGER = logging.getLogger(__name__)


class Orchestrator:
    """Coordinate source fetch, processing, and storage via queue workers.

    Fetch and process are isolated into two queues so network variability does not
    directly stall CPU-heavy ffmpeg work, and vice versa.
    """

    def __init__(
        self,
        source: SourcePort,
        processor: ProcessorPort,
        storage: StoragePort,
        repository: JobRepositoryPort,
        queue_policy: QueuePolicy | None = None,
        retention_policy: RetentionPolicy | None = None,
    ) -> None:
        self.source = source
        self.processor = processor
        self.storage = storage
        self.repository = repository
        self.queue_policy = queue_policy or QueuePolicy()
        self.retention_policy = retention_policy or RetentionPolicy()

        self.fetch_queue: asyncio.Queue[str | None] = asyncio.Queue(
            maxsize=self.queue_policy.max_queue_size
        )
        self.process_queue: asyncio.Queue[str | None] = asyncio.Queue(
            maxsize=self.queue_policy.max_queue_size
        )

        self._workers: list[asyncio.Task] = []
        self._job_contexts: dict[str, JobContext] = {}
        self._active_jobs: dict[str, Job] = {}
        self._enqueue_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._stop_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """Start background queue workers."""
        async with self._lifecycle_lock:
            if self._running:
                return
            self._running = True
            for _ in range(self.queue_policy.fetch_concurrency):
                self._workers.append(asyncio.create_task(self._fetch_worker()))
            for _ in range(self.queue_policy.process_concurrency):
                self._workers.append(asyncio.create_task(self._process_worker()))

    async def stop(self) -> None:
        """Stop background queue workers gracefully."""
        if self._stop_task is None or self._stop_task.done():
            self._stop_task = asyncio.create_task(self._stop_workers())
        await asyncio.shield(self._stop_task)

    async def _stop_workers(self) -> None:
        """Finish shutdown even if a caller stops waiting for it."""
        async with self._lifecycle_lock:
            async with self._enqueue_lock:
                if not self._running:
                    return
                self._running = False

            # Fetch workers must finish handing off jobs before processors exit.
            for _ in range(self.queue_policy.fetch_concurrency):
                await self.fetch_queue.put(None)
            await self.fetch_queue.join()
            for _ in range(self.queue_policy.process_concurrency):
                await self.process_queue.put(None)
            await self.process_queue.join()
            await asyncio.gather(*self._workers)
            self._workers.clear()

    async def enqueue(self, request: SourceRequest) -> Job | None:
        """Create a job and enqueue it to fetch queue."""
        async with self._enqueue_lock:
            if not self._running:
                return None
            job = await self.source.create_job(request)
            self._job_contexts[job.job_id] = JobContext(job_id=job.job_id)
            self._active_jobs[job.job_id] = job
            try:
                await self.repository.save(job)
                await self.fetch_queue.put(job.job_id)
            except (Exception, asyncio.CancelledError) as exc:
                try:
                    await self._fail_job(job, str(exc) or "Enqueue cancelled")
                finally:
                    await self._finalize_job_context(job.job_id)
                raise
            return job

    def metrics(self) -> dict[str, int | str | bool]:
        """Report activity from admitted jobs, including work outside the queues."""
        metrics = self.repository.metrics()
        terminal = {JobStatus.DONE, JobStatus.FAILED}
        current = next(
            (job.status.value for job in self._active_jobs.values() if job.status not in terminal),
            metrics["last_job_status"],
        )
        return {
            **metrics,
            "queue_fetch_size": self.fetch_queue.qsize(),
            "queue_process_size": self.process_queue.qsize(),
            "current_job_status": current,
            "is_busy": bool(self._active_jobs),
        }

    async def wait_for_idle(self) -> None:
        """Wait until all queued work is completed."""
        await self.fetch_queue.join()
        await self.process_queue.join()

    async def _fetch_worker(self) -> None:
        while True:
            job_id = await self.fetch_queue.get()
            try:
                if job_id is None:
                    return
                await self._run_fetch(job_id)
            finally:
                self.fetch_queue.task_done()

    async def _process_worker(self) -> None:
        while True:
            job_id = await self.process_queue.get()
            try:
                if job_id is None:
                    return
                await self._run_process(job_id)
            finally:
                self.process_queue.task_done()

    async def _run_fetch(self, job_id: str) -> None:
        job = await self.repository.get(job_id)
        if not job:
            await self._finalize_job_context(job_id)
            return

        handed_off = False
        try:
            while True:
                job.attempts_fetch += 1
                job.transition(JobStatus.FETCHING)
                await self.repository.update(job)
                try:
                    input_artifact = await asyncio.wait_for(
                        self.source.fetch(job, context=self._job_contexts[job_id]),
                        timeout=self.queue_policy.fetch_timeout_sec,
                    )
                    break
                except Exception as exc:
                    if job.attempts_fetch > self.queue_policy.retry_limit:
                        await self._fail_job(job, str(exc))
                        return
                    await asyncio.sleep(self.queue_policy.retry_backoff_sec)
            job.artifacts["input"] = input_artifact
            job.transition(JobStatus.FETCHED)
            await self.repository.update(job)
            await self.process_queue.put(job.job_id)
            handed_off = True
        finally:
            if not handed_off:
                await self._finalize_job_context(job_id)

    async def _run_process(self, job_id: str) -> None:
        job = await self.repository.get(job_id)
        if not job:
            await self._finalize_job_context(job_id)
            return

        try:
            input_artifact = job.artifacts.get("input")
            if input_artifact is None:
                await self._fail_job(job, "Missing input artifact")
                return

            while True:
                job.attempts_process += 1
                job.transition(JobStatus.PROCESSING)
                await self.repository.update(job)

                try:
                    output_artifact = await asyncio.wait_for(
                        self.processor.process(
                            job, input_artifact, context=self._job_contexts[job_id]
                        ),
                        timeout=self.queue_policy.process_timeout_sec,
                    )
                    break
                except Exception as exc:
                    if job.attempts_process > self.queue_policy.retry_limit:
                        await self._fail_job(job, str(exc))
                        return
                    await asyncio.sleep(self.queue_policy.retry_backoff_sec)

            job.artifacts["output"] = output_artifact
            job.transition(JobStatus.PROCESSED)
            await self.repository.update(job)
            try:
                storage_record = await self.storage.store(job, output_artifact)
            except Exception as exc:
                await self._fail_job(job, str(exc))
                return
            job.metadata["storage"] = {
                "location": storage_record.location,
                "stored_at": storage_record.stored_at.isoformat(),
            }
            job.transition(JobStatus.STORED)
            await self.repository.update(job)

            try:
                await self.storage.apply_policy(self.retention_policy)
            except Exception:
                _LOGGER.warning(
                    "Archive maintenance failed after storing job %s", job_id, exc_info=True
                )
            job.transition(JobStatus.DONE)
            await self.repository.update(job)
        finally:
            # Cleanup runs exactly once at orchestration boundary, regardless of
            # success, retries exhausted, or unexpected exceptions.
            await self._finalize_job_context(job_id)

    async def _finalize_job_context(self, job_id: str) -> None:
        context = self._job_contexts.pop(job_id, None)
        try:
            if context is not None:
                await context.cleanup_all()
        finally:
            self._active_jobs.pop(job_id, None)

    async def _fail_job(self, job: Job, message: str) -> None:
        job.error = message
        job.transition(JobStatus.FAILED)
        await self.repository.update(job)
