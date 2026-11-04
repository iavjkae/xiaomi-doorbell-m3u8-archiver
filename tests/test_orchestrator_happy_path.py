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


def test_orchestrator_happy_path() -> None:
    async def scenario() -> None:
        source = MockSource()
        processor = MockProcessor()
        storage = MockStorage()
        repo = MockRepository()
        orchestrator = Orchestrator(source, processor, storage, repo)

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
            assert "input" in saved.artifacts
            assert "output" in saved.artifacts
            assert storage.store_calls == 1
            assert storage.apply_policy_calls == 1
        finally:
            await orchestrator.stop()

    asyncio.run(scenario())
