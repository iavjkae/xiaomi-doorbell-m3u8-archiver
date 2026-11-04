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
from custom_components.xiaomi_doorbell_m3u8_archiver.core.domain import SourceRequest
from custom_components.xiaomi_doorbell_m3u8_archiver.core.orchestration import Orchestrator
from custom_components.xiaomi_doorbell_m3u8_archiver.core.policies import QueuePolicy


def test_enqueue_all_events_and_process_single_concurrency() -> None:
    async def scenario() -> None:
        source = MockSource()
        processor = MockProcessor(process_delay_sec=0.01)
        storage = MockStorage()
        repo = MockRepository()
        policy = QueuePolicy(
            fetch_concurrency=2,
            process_concurrency=1,
            retry_backoff_sec=0.001,
        )
        orchestrator = Orchestrator(source, processor, storage, repo, queue_policy=policy)

        await orchestrator.start()
        try:
            req = SourceRequest(
                source_type="ha_event",
                source_ref="camera.demo",
            )
            job1 = await orchestrator.enqueue(req)
            job2 = await orchestrator.enqueue(req)
            assert job1 is not None
            assert job2 is not None

            for i in range(5):
                await orchestrator.enqueue(
                    SourceRequest(
                        source_type="manual",
                        source_ref=f"manual://{i}",
                    )
                )

            await orchestrator.wait_for_idle()
            assert processor.max_active == 1
            assert storage.store_calls == 7
        finally:
            await orchestrator.stop()

    asyncio.run(scenario())
