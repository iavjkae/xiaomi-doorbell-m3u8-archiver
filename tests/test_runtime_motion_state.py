import asyncio
from unittest.mock import AsyncMock
from pathlib import Path

from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.mock.mock_storage import MockStorage
from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.mock.mock_processor import (
    MockProcessor,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.mock.mock_repository import (
    MockRepository,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.mock.mock_source import MockSource
from custom_components.xiaomi_doorbell_m3u8_archiver.const import (
    META_MOTION_VIDEO_TIME,
    META_STREAM_URL,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.core.domain import Job, SourceRequest
from custom_components.xiaomi_doorbell_m3u8_archiver.core.orchestration import Orchestrator
from custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.coordinator import (
    IntegrationRuntime,
)


def test_enqueue_request_skips_same_motion_time_for_ha_event() -> None:
    # Intent: prevent duplicate HA-event processing for replayed motion timestamp.
    # Expect: second enqueue returns None and only one archive write occurs.
    async def scenario() -> None:
        source = MockSource()
        processor = MockProcessor()
        storage = MockStorage()
        repo = MockRepository()
        orchestrator = Orchestrator(source, processor, storage, repo)
        runtime = IntegrationRuntime(orchestrator=orchestrator)

        await orchestrator.start()
        try:
            req = SourceRequest(
                source_type="ha_event",
                source_ref="camera.loock",
                payload={
                    META_STREAM_URL: "https://example.test/live.m3u8",
                    META_MOTION_VIDEO_TIME: "2026-02-27 10:00:00",
                },
            )
            first = await runtime.enqueue_request(req)
            second = await runtime.enqueue_request(req)
            assert first is not None
            assert second is None

            await orchestrator.wait_for_idle()
            assert storage.store_calls == 1
        finally:
            await orchestrator.stop()

    asyncio.run(scenario())


def test_enqueue_request_allows_new_motion_time() -> None:
    # Intent: ensure real motion updates are not suppressed by cursor state.
    # Expect: distinct motion timestamps both enqueue and both complete.
    async def scenario() -> None:
        source = MockSource()
        processor = MockProcessor()
        storage = MockStorage()
        repo = MockRepository()
        orchestrator = Orchestrator(source, processor, storage, repo)
        runtime = IntegrationRuntime(orchestrator=orchestrator)

        await orchestrator.start()
        try:
            req1 = SourceRequest(
                source_type="ha_event",
                source_ref="camera.loock",
                payload={
                    META_STREAM_URL: "https://example.test/1.m3u8",
                    META_MOTION_VIDEO_TIME: "t1",
                },
            )
            req2 = SourceRequest(
                source_type="ha_event",
                source_ref="camera.loock",
                payload={
                    META_STREAM_URL: "https://example.test/2.m3u8",
                    META_MOTION_VIDEO_TIME: "t2",
                },
            )

            first = await runtime.enqueue_request(req1)
            second = await runtime.enqueue_request(req2)
            assert first is not None
            assert second is not None

            await orchestrator.wait_for_idle()
            assert storage.store_calls == 2
        finally:
            await orchestrator.stop()

    asyncio.run(scenario())


def test_manual_enqueue_does_not_update_motion_cursor() -> None:
    # Intent: keep operator-triggered manual jobs independent from HA-event cursor.
    # Expect: manual requests always enqueue and stored cursor remains unchanged.
    async def scenario() -> None:
        source = MockSource()
        processor = MockProcessor()
        storage = MockStorage()
        repo = MockRepository()
        orchestrator = Orchestrator(source, processor, storage, repo)
        runtime = IntegrationRuntime(orchestrator=orchestrator)
        runtime.last_motion_by_entity = {"camera.loock": "keep-me"}

        await orchestrator.start()
        try:
            req = SourceRequest(
                source_type="manual",
                source_ref="camera.loock",
                payload={
                    META_STREAM_URL: "https://example.test/live.m3u8",
                    META_MOTION_VIDEO_TIME: "manual-time",
                },
            )
            first = await runtime.enqueue_request(req)
            second = await runtime.enqueue_request(req)
            assert first is not None
            assert second is not None

            await orchestrator.wait_for_idle()
            assert storage.store_calls == 2
            assert runtime.last_motion_by_entity == {"camera.loock": "keep-me"}
        finally:
            await orchestrator.stop()

    asyncio.run(scenario())


def test_load_motion_cursor_from_storage_skips_replay_after_restart(
    tmp_path: Path, monkeypatch
) -> None:
    # Intent: verify restart-safe replay suppression by loading persisted cursor.
    # Expect: persisted timestamp is skipped, newer timestamp enqueues successfully.
    async def scenario() -> None:
        class FakeStore:
            def __init__(self, hass, version, key) -> None:
                self.hass = hass
                self.version = version
                self.key = key
                self.hass["store_key"] = key

            async def async_load(self):
                return {
                    "last_motion_by_entity": {
                        "camera.loock": "2026-02-27 10:00:00",
                    }
                }

            async def async_save(self, data) -> None:
                self.hass["saved"] = data

        monkeypatch.setattr(
            "custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.coordinator.Store",
            FakeStore,
        )

        hass = {}
        runtime = await IntegrationRuntime.async_from_config(
            hass,
            "entry-123",
            {
                "fetch_concurrency": 1,
                "process_concurrency": 1,
                "max_queue_size": 10,
                "retry_limit": 1,
                "retry_backoff_sec": 0.01,
                "ffmpeg_path": "ffmpeg",
                "storage_root": str(tmp_path / "storage"),
            },
        )

        req_same = SourceRequest(
            source_type="ha_event",
            source_ref="camera.loock",
            payload={
                META_STREAM_URL: "https://example.test/live.m3u8",
                META_MOTION_VIDEO_TIME: "2026-02-27 10:00:00",
            },
        )
        req_new = SourceRequest(
            source_type="ha_event",
            source_ref="camera.loock",
            payload={
                META_STREAM_URL: "https://example.test/live.m3u8",
                META_MOTION_VIDEO_TIME: "2026-02-27 10:00:01",
            },
        )

        runtime.orchestrator.enqueue = AsyncMock(
            return_value=Job(job_id="new-job", source_ref="camera.loock")
        )
        skipped = await runtime.enqueue_request(req_same)
        enqueued = await runtime.enqueue_request(req_new)

        assert skipped is None
        assert enqueued is not None
        runtime.orchestrator.enqueue.assert_awaited_once_with(req_new)
        assert hass["store_key"] == "xiaomi_doorbell_m3u8_archiver_state_entry-123"
        assert hass["saved"] == {"last_motion_by_entity": {"camera.loock": "2026-02-27 10:00:01"}}

    asyncio.run(scenario())
