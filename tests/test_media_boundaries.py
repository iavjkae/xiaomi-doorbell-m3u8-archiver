"""Archive read contracts and thumbnail transport boundaries."""

import asyncio
from datetime import date, datetime, timezone
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from homeassistant.components.media_source import MediaSourceItem

from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.storage.local_fs_storage import (
    LocalFsStorage,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.processor.thumbnail_service import (
    ThumbnailService,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.core.ports.media_port import (
    ThumbnailGenerationError,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.const import DOMAIN
from custom_components.xiaomi_doorbell_m3u8_archiver.core.domain.archived_video import ArchivedVideo
from custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.media_source import (
    ArchivedVideoMediaSource,
    ArchivedVideoThumbnailView,
    ArchivedVideoView,
)
from custom_components.xiaomi_doorbell_m3u8_archiver import async_setup_entry


async def inline_io(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Keep small filesystem fixtures deterministic without executor threads."""
    return func(*args, **kwargs)


def test_entry_setup_injects_shared_thumbnail_budget_and_configured_legacy_sources() -> None:
    async def scenario() -> None:
        configured = [
            SimpleNamespace(data={"camera_entity_id": source})
            for source in ("camera.front_door", "camera.front__door")
        ]
        hass = SimpleNamespace(
            data={},
            config_entries=SimpleNamespace(
                async_forward_entry_setups=AsyncMock(), async_entries=lambda _domain: configured
            ),
        )
        runtimes = [SimpleNamespace(start=AsyncMock()) for _ in range(2)]
        with (
            patch(
                "custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.coordinator.IntegrationRuntime.async_from_config",
                new=AsyncMock(side_effect=runtimes),
            ) as factory,
            patch(
                "custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.entity.async_register_archive_device"
            ),
            patch(
                "custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.services.async_register_services",
                new=AsyncMock(),
            ),
        ):
            for index in range(2):
                entry = SimpleNamespace(entry_id=f"entry-{index}", data={}, title="Archive")
                assert await async_setup_entry(hass, entry)
        first = factory.call_args_list[0].kwargs["thumbnail_slots"]
        second = factory.call_args_list[1].kwargs["thumbnail_slots"]
        assert first is second is hass.data[DOMAIN]["thumbnail_slots"]
        assert isinstance(first, asyncio.Semaphore)
        sources = factory.call_args_list[0].kwargs["legacy_sources"]
        assert sources is factory.call_args_list[1].kwargs["legacy_sources"]
        assert sources == {"camera.front_door", "camera.front__door"}

    asyncio.run(scenario())


def test_catalog_resolves_old_footage_without_a_clock(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = LocalFsStorage(tmp_path)
        folder = tmp_path / "2026/09"
        folder.mkdir(parents=True)
        video = folder / "camera_front_2026_09_02_23_59_59_01.mp4"
        video.write_bytes(b"video")
        invalid = folder / "camera_front_2026_09_02_invalid.mp4"
        invalid.write_bytes(b"invalid")
        with patch("asyncio.to_thread", new=inline_io):
            records = await storage.videos_for_day(date(2026, 9, 2), "camera.front")
            resolved = await storage.resolve_video("camera.front", video.name)
        assert resolved == video
        assert len(records) == 1
        assert records[0].captured_at.isoformat() == "2026-09-02T23:59:59+00:00"
        assert records[0].identifier == video.name

    asyncio.run(scenario())


def test_catalog_parses_each_video_filename_once(tmp_path: Path) -> None:
    storage = LocalFsStorage(tmp_path)
    folder = tmp_path / "2026/10"
    folder.mkdir(parents=True)
    video = folder / "camera.front_2026_10_19_10_00_00.mp4"
    video.write_bytes(b"video")

    with patch.object(storage, "_capture_time", wraps=storage._capture_time) as parse:
        records = storage._videos_for_day_sync(date(2026, 10, 19), "camera.front")

    assert [record.path for record in records] == [video]
    parse.assert_called_once_with("camera.front", video.name)


def test_catalog_rejects_symlink_escape_and_invalid_identifiers(tmp_path: Path) -> None:
    storage = LocalFsStorage(tmp_path / "archive")
    folder = tmp_path / "archive/2026/09"
    folder.mkdir(parents=True)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"private")
    link = folder / "camera_front_2026_09_02_09_00_00.mp4"
    link.symlink_to(outside)
    for identifier in (link.name, "../outside.mp4", "camera_front_2026_99_02_09_00_00.mp4"):
        assert storage._resolve_video_sync("camera.front", identifier) is None
    assert storage._videos_for_day_sync(date(2026, 9, 2), "camera.front") == []

    inside = folder / "camera_front_2026_09_02_10_00_00.mp4"
    inside.write_bytes(b"video")
    link.unlink()
    link.symlink_to(inside)
    assert storage._resolve_video_sync("camera.front", link.name) == link
    assert {
        video.path for video in storage._videos_for_day_sync(date(2026, 9, 2), "camera.front")
    } == {link, inside}


def test_media_resolves_previous_day_without_an_ingestion_orchestrator(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = LocalFsStorage(tmp_path)
        folder = tmp_path / "2026/09"
        folder.mkdir(parents=True)
        video = folder / "camera_front_2026_09_02_23_59_59.mp4"
        video.write_bytes(b"video")
        extractor = SimpleNamespace(extract_thumbnail=AsyncMock(return_value=b"jpeg"))
        runtime = SimpleNamespace(archive=storage, thumbnails=ThumbnailService(extractor))
        hass = SimpleNamespace(
            config=SimpleNamespace(language="en"),
            data={
                DOMAIN: {
                    "runtimes": {"entry": runtime},
                    "entry_camera_entity": {"entry": "camera.front"},
                }
            },
        )
        source = ArchivedVideoMediaSource(hass)
        with (
            patch("asyncio.to_thread", new=inline_io),
            patch(
                "custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.media_source.dt_util.now",
                return_value=datetime(2026, 9, 3, tzinfo=timezone.utc),
            ),
        ):
            browse = await source.async_browse_media(MediaSourceItem(hass, DOMAIN, "", None))
            assert not browse.children
            resolved = await source.async_resolve_media(
                MediaSourceItem(hass, DOMAIN, f"video/entry/{video.name}", None)
            )
            assert resolved.path == video
            assert await source.async_thumbnail("entry", video.name) == b"jpeg"

    asyncio.run(scenario())


def test_media_orders_capture_metadata_not_file_identifiers(tmp_path: Path) -> None:
    async def scenario() -> None:
        early = ArchivedVideo(
            "z.mp4", datetime(2026, 9, 2, 8, tzinfo=timezone.utc), tmp_path / "z.mp4"
        )
        late = ArchivedVideo(
            "a.mp4", datetime(2026, 9, 2, 9, tzinfo=timezone.utc), tmp_path / "a.mp4"
        )
        catalog = SimpleNamespace(videos_for_day=AsyncMock(return_value=[late, early]))
        hass = SimpleNamespace(
            config=SimpleNamespace(language="en"),
            data={
                DOMAIN: {
                    "runtimes": {"entry": SimpleNamespace(archive=catalog)},
                    "entry_camera_entity": {"entry": "camera.front"},
                }
            },
        )
        source = ArchivedVideoMediaSource(hass)
        with patch(
            "custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.media_source.async_sign_path",
            side_effect=lambda _hass, path, _expiry: path,
        ):
            media = await source.async_browse_media(MediaSourceItem(hass, DOMAIN, "", None))
        assert [child.identifier for child in media.children] == [
            "video/entry/z.mp4",
            "video/entry/a.mp4",
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize("service_count", [1, 2, 3])
def test_thumbnail_service_limits_parallel_extractions(tmp_path: Path, service_count: int) -> None:
    async def scenario() -> None:
        active = 0
        peak = 0

        class Extractor:
            async def extract_thumbnail(self, video_path: Path) -> bytes:
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                try:
                    await asyncio.sleep(0)
                    return b"jpeg"
                finally:
                    active -= 1

        slots = asyncio.Semaphore(2)
        services = [ThumbnailService(Extractor(), slots=slots) for _ in range(service_count)]
        videos = [tmp_path / f"{index}.mp4" for index in range(5)]
        for video in videos:
            video.write_bytes(b"video")
        with patch("asyncio.to_thread", new=inline_io):
            assert (
                await asyncio.gather(
                    *(
                        services[index % service_count].get(video)
                        for index, video in enumerate(videos)
                    )
                )
                == [b"jpeg"] * 5
            )
        assert peak == 2
        assert all(not service._locks for service in services)

    asyncio.run(scenario())


def test_thumbnail_service_deduplicates_and_invalidates_cache(tmp_path: Path) -> None:
    async def scenario() -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"video")
        calls = 0

        class Extractor:
            async def extract_thumbnail(self, video_path: Path) -> bytes:
                nonlocal calls
                assert video_path == video
                calls += 1
                await asyncio.sleep(0)
                return b"jpeg"

        service = ThumbnailService(Extractor())
        with patch("asyncio.to_thread", new=inline_io):
            assert await asyncio.gather(service.get(video), service.get(video)) == [b"jpeg"] * 2
            assert calls == 1
            stat = video.stat()
            os.utime(video, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            assert await service.get(video) == b"jpeg"
        assert calls == 2
        assert len(service._cache) == 1
        assert not service._locks

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure", [RuntimeError("decode"), FileNotFoundError("ffmpeg"), OSError("encoder")]
)
def test_thumbnail_generation_errors_are_not_missing_files(
    tmp_path: Path, failure: Exception
) -> None:
    async def scenario() -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"video")
        extractor = SimpleNamespace(extract_thumbnail=AsyncMock(side_effect=failure))
        service = ThumbnailService(extractor)
        with patch("asyncio.to_thread", new=inline_io):
            with pytest.raises(ThumbnailGenerationError):
                await service.get(video)
        assert not service._cache

    asyncio.run(scenario())


def test_missing_video_does_not_start_extraction(tmp_path: Path) -> None:
    async def scenario() -> None:
        extractor = SimpleNamespace(extract_thumbnail=AsyncMock())
        service = ThumbnailService(extractor)
        with patch("asyncio.to_thread", new=inline_io):
            with pytest.raises(FileNotFoundError):
                await service.get(tmp_path / "missing.mp4")
        extractor.extract_thumbnail.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (FileNotFoundError(), 404),
        (ThumbnailGenerationError("decode"), 503),
        (PermissionError(), 503),
    ],
)
def test_thumbnail_http_maps_failure_types(failure: Exception, status: int) -> None:
    async def scenario() -> None:
        source = SimpleNamespace(async_thumbnail=AsyncMock(side_effect=failure))
        view = ArchivedVideoThumbnailView(source)
        with pytest.raises(web.HTTPException) as result:
            await view.get(None, "entry", "video.mp4")
        assert result.value.status == status
        if status == 503:
            assert result.value.headers["Retry-After"] == "30"

    asyncio.run(scenario())


def test_thumbnail_http_serves_jpeg_and_reports_unresolved_video() -> None:
    async def scenario() -> None:
        source = SimpleNamespace(async_thumbnail=AsyncMock(return_value=b"jpeg"))
        view = ArchivedVideoThumbnailView(source)
        response = await view.get(None, "entry", "video.mp4")
        assert response.content_type == "image/jpeg"
        assert response.body == b"jpeg"
        source.async_thumbnail.return_value = None
        with pytest.raises(web.HTTPNotFound):
            await view.get(None, "entry", "video.mp4")

    asyncio.run(scenario())


def test_video_http_head_matches_get_headers_and_preserves_ranges(tmp_path: Path) -> None:
    async def scenario() -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"test-video")
        source = SimpleNamespace(async_resolve_video=AsyncMock(return_value=video))
        view = ArchivedVideoView(source)

        async def handle(request: web.Request) -> web.StreamResponse:
            handler = view.head if request.method == "HEAD" else view.get
            return await handler(request, "entry", "video.mp4")

        app = web.Application()
        app.router.add_route("*", "/video.mp4", handle)
        async with TestClient(TestServer(app)) as client:
            async with client.get("/video.mp4") as response:
                assert await response.read() == b"test-video"
                headers = response.headers
            async with client.head("/video.mp4") as response:
                assert response.status == 200
                assert await response.read() == b""
                for name in ("Content-Type", "Content-Length", "ETag", "Last-Modified"):
                    assert response.headers[name] == headers[name]
            async with client.get("/video.mp4", headers={"Range": "bytes=0-3"}) as response:
                assert response.status == 206
                assert await response.read() == b"test"
            source.async_resolve_video.return_value = None
            for method in (client.get, client.head):
                async with method("/video.mp4") as response:
                    assert response.status == 404

    asyncio.run(scenario())
