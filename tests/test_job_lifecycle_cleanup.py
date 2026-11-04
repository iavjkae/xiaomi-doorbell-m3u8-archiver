import asyncio
from pathlib import Path

from custom_components.xiaomi_doorbell_m3u8_archiver.core.domain import (
    Artifact,
    Job,
    SourceRequest,
    StorageRecord,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.core.lifecycle import (
    DirectoryCleanupHandle,
    JobContext,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.core.orchestration import (
    Orchestrator,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.core.policies import QueuePolicy
from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.mock.mock_repository import (
    MockRepository,
)


class TempDirSource:
    def __init__(self, temp_root: Path) -> None:
        self._temp_root = temp_root
        self._counter = 0

    async def create_job(self, request: SourceRequest) -> Job:
        self._counter += 1
        return Job(job_id=f"job-{self._counter:04d}", source_ref=request.source_ref)

    async def fetch(self, job: Job, *, context: JobContext | None = None) -> Artifact:
        job_dir = self._temp_root / job.job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        input_path = job_dir / "input.m3u8"
        input_path.write_text("#EXTM3U\n", encoding="utf-8")

        if context is not None:
            context.register_cleanup(DirectoryCleanupHandle(path=job_dir))

        return Artifact(
            kind="input", uri_or_path=str(input_path), meta={"job_temp_dir": str(job_dir)}
        )


class SuccessProcessor:
    async def process(
        self, job: Job, input_artifact: Artifact, *, context: JobContext | None = None
    ) -> Artifact:
        output_path = Path(input_artifact.uri_or_path).parent / "processed.mp4"
        output_path.write_bytes(b"video")
        return Artifact(kind="output", uri_or_path=str(output_path))


class FailingProcessor:
    async def process(
        self, job: Job, input_artifact: Artifact, *, context: JobContext | None = None
    ) -> Artifact:
        raise RuntimeError("processing failed")


class NoopStorage:
    async def store(self, job: Job, output_artifact: Artifact) -> StorageRecord:
        return StorageRecord(location="storage://ok")

    async def apply_policy(self, policy) -> dict:
        return {"deleted": 0}


def test_temp_dir_cleaned_after_success(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = TempDirSource(tmp_path)
        repo = MockRepository()
        orchestrator = Orchestrator(
            source=source,
            processor=SuccessProcessor(),
            storage=NoopStorage(),
            repository=repo,
            queue_policy=QueuePolicy(retry_limit=0),
        )

        await orchestrator.start()
        try:
            job = await orchestrator.enqueue(
                SourceRequest(source_type="manual", source_ref="camera.demo")
            )
            assert job is not None
            await orchestrator.wait_for_idle()
            assert not (tmp_path / job.job_id).exists()
        finally:
            await orchestrator.stop()

    asyncio.run(scenario())


def test_temp_dir_cleaned_after_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = TempDirSource(tmp_path)
        repo = MockRepository()
        orchestrator = Orchestrator(
            source=source,
            processor=FailingProcessor(),
            storage=NoopStorage(),
            repository=repo,
            queue_policy=QueuePolicy(retry_limit=0),
        )

        await orchestrator.start()
        try:
            job = await orchestrator.enqueue(
                SourceRequest(source_type="manual", source_ref="camera.demo")
            )
            assert job is not None
            await orchestrator.wait_for_idle()
            assert not (tmp_path / job.job_id).exists()
            assert orchestrator.metrics()["total_failed"] == 1
            assert orchestrator.metrics()["total_done"] == 0
        finally:
            await orchestrator.stop()

    asyncio.run(scenario())
