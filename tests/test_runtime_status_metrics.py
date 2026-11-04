import asyncio

from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.mock.mock_storage import MockStorage
from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.mock.mock_processor import (
    MockProcessor,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.mock.mock_source import MockSource
from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.repository.runtime_repository import (
    RuntimeRepository,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.core.domain import (
    JobStatus,
    SourceRequest,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.core.policies import RetentionPolicy
from custom_components.xiaomi_doorbell_m3u8_archiver.core.orchestration import (
    Orchestrator,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.coordinator import (
    IntegrationRuntime,
)


def test_metrics_report_idle_and_busy_states() -> None:
    async def scenario() -> None:
        repo = RuntimeRepository()
        orchestrator = Orchestrator(MockSource(), MockProcessor(), MockStorage(), repo)
        runtime = IntegrationRuntime(orchestrator=orchestrator)

        metrics_idle = runtime.metrics()
        assert metrics_idle["current_job_status"] == metrics_idle["last_job_status"]
        assert metrics_idle["is_busy"] is False
        assert metrics_idle["processed_today"] == 0

        await orchestrator.start()
        await orchestrator.enqueue(SourceRequest(source_type="manual", source_ref="camera.demo"))
        metrics_busy = runtime.metrics()
        assert metrics_busy["is_busy"] is True

        await orchestrator.wait_for_idle()
        await orchestrator.stop()

    asyncio.run(scenario())


def test_metrics_busy_for_inflight_terminal_transition_statuses() -> None:
    # Intent: transitional statuses should not report idle too early.
    # Expect: STORED reports busy, DONE reports idle.
    async def scenario() -> None:
        stored = asyncio.Event()
        release = asyncio.Event()

        class Storage(MockStorage):
            async def apply_policy(self, policy: RetentionPolicy) -> dict:
                stored.set()
                await release.wait()
                return {}

        repo = RuntimeRepository()
        orchestrator = Orchestrator(MockSource(), MockProcessor(), Storage(), repo)
        runtime = IntegrationRuntime(orchestrator=orchestrator)
        await orchestrator.start()
        await orchestrator.enqueue(SourceRequest(source_type="manual", source_ref="camera.demo"))
        await asyncio.wait_for(stored.wait(), timeout=1)
        metrics_stored = runtime.metrics()
        assert metrics_stored["current_job_status"] == JobStatus.STORED.value
        assert metrics_stored["is_busy"] is True

        release.set()
        await orchestrator.wait_for_idle()
        await orchestrator.stop()
        metrics_done = runtime.metrics()
        assert metrics_done["current_job_status"] == JobStatus.DONE.value
        assert metrics_done["is_busy"] is False

    asyncio.run(scenario())
