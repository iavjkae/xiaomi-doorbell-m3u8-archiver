import asyncio

from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.mock.mock_storage import (
    MockStorage,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.mock.mock_processor import (
    MockProcessor,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.mock.mock_repository import (
    MockRepository,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.mock.mock_source import (
    MockSource,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.core.domain import (
    JobStatus,
    SourceRequest,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.core.orchestration import Orchestrator
from custom_components.xiaomi_doorbell_m3u8_archiver.core.policies import QueuePolicy


def test_retry_then_success() -> None:
    async def scenario() -> None:
        source = MockSource(fail_fetch_times=1)
        processor = MockProcessor(fail_process_times=1)
        storage = MockStorage()
        repo = MockRepository()
        policy = QueuePolicy(retry_limit=1, retry_backoff_sec=0.001)
        orchestrator = Orchestrator(source, processor, storage, repo, queue_policy=policy)

        await orchestrator.start()
        try:
            job = await orchestrator.enqueue(
                SourceRequest(source_type="ha_event", source_ref="camera.demo")
            )
            assert job is not None
            await orchestrator.wait_for_idle()

            saved = await repo.get(job.job_id)
            assert saved is not None
            assert saved.status == JobStatus.DONE
            assert saved.attempts_fetch == 2
            assert saved.attempts_process == 2
            assert orchestrator.metrics()["total_done"] == 1
            assert orchestrator.metrics()["total_failed"] == 0
        finally:
            await orchestrator.stop()

    asyncio.run(scenario())
