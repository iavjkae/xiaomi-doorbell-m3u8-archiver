"""Regression coverage for pipeline ownership and shutdown boundaries."""

import asyncio

import pytest

from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.mock.mock_processor import (
    MockProcessor,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.mock.mock_source import MockSource
from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.mock.mock_storage import MockStorage
from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.repository.runtime_repository import (
    RuntimeRepository,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.core.domain import (
    Artifact,
    Job,
    JobStatus,
    SourceRequest,
    StorageRecord,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.core.lifecycle import JobContext
from custom_components.xiaomi_doorbell_m3u8_archiver.core.orchestration import Orchestrator
from custom_components.xiaomi_doorbell_m3u8_archiver.core.policies import (
    QueuePolicy,
    RetentionPolicy,
)


def test_stop_drains_both_stages_and_admitted_enqueue_with_full_queues() -> None:
    async def scenario() -> None:
        started, release = asyncio.Event(), asyncio.Event()

        class Source(MockSource):
            async def fetch(self, job: Job, *, context: JobContext | None = None) -> Artifact:
                started.set()
                await release.wait()
                return await super().fetch(job, context=context)

        source, processor, storage = Source(), MockProcessor(), MockStorage()
        orchestrator = Orchestrator(
            source,
            processor,
            storage,
            RuntimeRepository(),
            QueuePolicy(fetch_concurrency=1, process_concurrency=1, max_queue_size=1),
        )
        request = SourceRequest(source_type="manual", source_ref="camera.demo")
        assert await orchestrator.enqueue(request) is None
        await orchestrator.start()
        first = await orchestrator.enqueue(request)
        await started.wait()
        second = await orchestrator.enqueue(request)
        third_enqueue = asyncio.create_task(orchestrator.enqueue(request))
        await asyncio.sleep(0)
        assert not third_enqueue.done()
        stopping = asyncio.create_task(orchestrator.stop())
        release.set()
        third = await third_enqueue
        while orchestrator._running:
            await asyncio.sleep(0)
        assert await orchestrator.enqueue(request) is None
        await asyncio.gather(stopping, orchestrator.stop())
        assert all(job.status == JobStatus.DONE for job in (first, second, third))
        assert processor.process_calls == storage.store_calls == 3
        assert not orchestrator._active_jobs
        assert not orchestrator._job_contexts
        assert not orchestrator._workers
        assert orchestrator.fetch_queue.empty()
        assert orchestrator.process_queue.empty()
        await orchestrator.wait_for_idle()
        await orchestrator.start()
        restarted = await orchestrator.enqueue(request)
        await orchestrator.stop()
        assert restarted.status == JobStatus.DONE

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def test_maintenance_failure_does_not_retry_processing_or_storage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        class Storage(MockStorage):
            async def apply_policy(self, policy: RetentionPolicy) -> dict:
                self.apply_policy_calls += 1
                raise OSError("retention unavailable")

        processor, storage, repository = MockProcessor(), Storage(), RuntimeRepository()
        orchestrator = Orchestrator(MockSource(), processor, storage, repository)
        await orchestrator.start()
        job = await orchestrator.enqueue(SourceRequest("manual", "camera.demo"))
        await orchestrator.stop()
        assert job.status == JobStatus.DONE
        assert processor.process_calls == storage.store_calls == storage.apply_policy_calls == 1
        assert repository.total_done == 1
        assert repository.total_failed == 0
        assert "Archive maintenance failed" in caplog.text

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def test_storage_failure_does_not_repeat_a_potentially_completed_write() -> None:
    async def scenario() -> None:
        class Storage(MockStorage):
            async def store(self, job: Job, output_artifact: Artifact) -> StorageRecord:
                await super().store(job, output_artifact)
                raise OSError("write outcome uncertain")

        processor, storage, repository = MockProcessor(), Storage(), RuntimeRepository()
        orchestrator = Orchestrator(MockSource(), processor, storage, repository)
        await orchestrator.start()
        job = await orchestrator.enqueue(SourceRequest("manual", "camera.demo"))
        await orchestrator.stop()
        assert job.status == JobStatus.FAILED
        assert job.error == "write outcome uncertain"
        assert processor.process_calls == storage.store_calls == 1
        assert storage.apply_policy_calls == 0
        assert repository.total_failed == 1

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def test_metrics_remain_busy_when_another_job_finishes() -> None:
    async def scenario() -> None:
        processing, release = asyncio.Event(), asyncio.Event()

        class Processor(MockProcessor):
            async def process(
                self, job: Job, input_artifact: Artifact, *, context: JobContext | None = None
            ) -> Artifact:
                if job.job_id == "job-0001":
                    processing.set()
                    await release.wait()
                return await super().process(job, input_artifact, context=context)

        orchestrator = Orchestrator(
            MockSource(),
            Processor(),
            MockStorage(),
            RuntimeRepository(),
            QueuePolicy(process_concurrency=2),
        )
        await orchestrator.start()
        first = await orchestrator.enqueue(SourceRequest("manual", "camera.demo"))
        await processing.wait()
        second = await orchestrator.enqueue(SourceRequest("manual", "camera.demo"))
        while second.status != JobStatus.DONE:
            await asyncio.sleep(0)
        metrics = orchestrator.metrics()
        assert metrics["queue_fetch_size"] == metrics["queue_process_size"] == 0
        assert metrics["last_job_status"] == "done"
        assert metrics["current_job_status"] == first.status.value == "processing"
        assert metrics["is_busy"] is True
        release.set()
        await orchestrator.stop()
        assert orchestrator.metrics()["is_busy"] is False

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def test_cleanup_context_is_explicit_shared_and_released_once() -> None:
    async def scenario() -> None:
        contexts: list[JobContext] = []
        cleaned: list[str] = []

        class Handle:
            name = "test"

            async def cleanup(self) -> None:
                cleaned.append(self.name)

        class Source(MockSource):
            async def fetch(self, job: Job, *, context: JobContext | None = None) -> Artifact:
                assert context is not None
                contexts.append(context)
                context.register_cleanup(Handle())
                return await super().fetch(job, context=context)

        class Processor(MockProcessor):
            async def process(
                self, job: Job, input_artifact: Artifact, *, context: JobContext | None = None
            ) -> Artifact:
                assert context is contexts[0]
                return await super().process(job, input_artifact, context=context)

        orchestrator = Orchestrator(Source(), Processor(), MockStorage(), RuntimeRepository())
        await orchestrator.start()
        job = await orchestrator.enqueue(SourceRequest("manual", "camera.demo"))
        await orchestrator.stop()
        assert job.status == JobStatus.DONE
        assert "_job_context" not in job.metadata
        assert cleaned == ["test"]
        assert not orchestrator._job_contexts

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


@pytest.mark.parametrize("stage", ["fetch", "process"])
def test_timeouts_release_the_explicit_context(stage: str) -> None:
    async def scenario() -> None:
        cleaned: list[str] = []

        class Handle:
            name = "test"

            async def cleanup(self) -> None:
                cleaned.append(self.name)

        class Source(MockSource):
            async def fetch(self, job: Job, *, context: JobContext | None = None) -> Artifact:
                assert context is not None
                context.register_cleanup(Handle())
                if stage == "fetch":
                    await asyncio.Event().wait()
                return await super().fetch(job, context=context)

        class Processor(MockProcessor):
            async def process(
                self, job: Job, input_artifact: Artifact, *, context: JobContext | None = None
            ) -> Artifact:
                await asyncio.Event().wait()
                return await super().process(job, input_artifact, context=context)

        repository = RuntimeRepository()
        orchestrator = Orchestrator(
            Source(),
            Processor(),
            MockStorage(),
            repository,
            QueuePolicy(retry_limit=0, fetch_timeout_sec=0.01, process_timeout_sec=0.01),
        )
        await orchestrator.start()
        job = await orchestrator.enqueue(SourceRequest("manual", "camera.demo"))
        assert job is not None
        await orchestrator.stop()
        assert job.status == JobStatus.FAILED
        assert job.error is not None
        assert await repository.get(job.job_id) is job
        assert repository.total_failed == 1
        assert cleaned == ["test"]
        assert not orchestrator._active_jobs
        assert not orchestrator._job_contexts

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def test_cancelled_enqueue_does_not_leave_an_active_job() -> None:
    async def scenario() -> None:
        started, release = asyncio.Event(), asyncio.Event()

        class Source(MockSource):
            async def fetch(self, job: Job, *, context: JobContext | None = None) -> Artifact:
                started.set()
                await release.wait()
                return await super().fetch(job, context=context)

        repository = RuntimeRepository()
        orchestrator = Orchestrator(
            Source(),
            MockProcessor(),
            MockStorage(),
            repository,
            QueuePolicy(fetch_concurrency=1, max_queue_size=1),
        )
        await orchestrator.start()
        request = SourceRequest("manual", "camera.demo")
        await orchestrator.enqueue(request)
        await started.wait()
        await orchestrator.enqueue(request)
        pending = asyncio.create_task(orchestrator.enqueue(request))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert len(orchestrator._active_jobs) == len(orchestrator._job_contexts) == 2
        assert repository.total_failed == 1
        release.set()
        await orchestrator.stop()
        assert not orchestrator._active_jobs
        assert repository.total_done == 2

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


@pytest.mark.parametrize("stage", ["fetch", "process"])
def test_cancelled_stop_can_be_awaited_again_without_leaking_workers(stage: str) -> None:
    async def scenario() -> None:
        started, release = asyncio.Event(), asyncio.Event()

        class Source(MockSource):
            async def fetch(self, job: Job, *, context: JobContext | None = None) -> Artifact:
                if stage == "fetch":
                    started.set()
                    await release.wait()
                return await super().fetch(job, context=context)

        class Processor(MockProcessor):
            async def process(
                self, job: Job, input_artifact: Artifact, *, context: JobContext | None = None
            ) -> Artifact:
                if stage == "process":
                    started.set()
                    await release.wait()
                return await super().process(job, input_artifact, context=context)

        orchestrator = Orchestrator(Source(), Processor(), MockStorage(), RuntimeRepository())
        await orchestrator.start()
        job = await orchestrator.enqueue(SourceRequest("manual", "camera.demo"))
        await started.wait()
        stopping = asyncio.create_task(orchestrator.stop())
        while orchestrator._running:
            await asyncio.sleep(0)
        shutdown = orchestrator._stop_task
        workers = list(orchestrator._workers)
        stopping.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopping
        assert not shutdown.done()
        retry = asyncio.create_task(orchestrator.stop())
        await asyncio.sleep(0)
        assert orchestrator._stop_task is shutdown
        release.set()
        await retry
        assert job.status == JobStatus.DONE
        assert all(worker.done() for worker in workers)
        assert not orchestrator._workers
        assert not orchestrator._active_jobs
        assert not orchestrator._job_contexts
        await orchestrator.wait_for_idle()

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def test_cancelled_stop_finishes_even_without_another_waiter() -> None:
    async def scenario() -> None:
        started, release = asyncio.Event(), asyncio.Event()

        class Source(MockSource):
            async def fetch(self, job: Job, *, context: JobContext | None = None) -> Artifact:
                started.set()
                await release.wait()
                return await super().fetch(job, context=context)

        orchestrator = Orchestrator(Source(), MockProcessor(), MockStorage(), RuntimeRepository())
        await orchestrator.start()
        await orchestrator.enqueue(SourceRequest("manual", "camera.demo"))
        await started.wait()
        stopping = asyncio.create_task(orchestrator.stop())
        while orchestrator._running:
            await asyncio.sleep(0)
        stopping.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopping
        release.set()
        await orchestrator._stop_task
        assert not orchestrator._workers
        await orchestrator.start()
        restarted = await orchestrator.enqueue(SourceRequest("manual", "camera.demo"))
        await orchestrator.stop()
        assert restarted.status == JobStatus.DONE

    asyncio.run(asyncio.wait_for(scenario(), timeout=3))
