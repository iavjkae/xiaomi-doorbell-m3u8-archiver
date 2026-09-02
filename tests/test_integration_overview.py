import asyncio
from datetime import date, datetime, timezone
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.components.media_player import BrowseError, MediaClass
from homeassistant.components.media_source import MediaSourceItem, Unresolvable

from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.storage.local_fs_storage import (
    LocalFsStorage,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.const import DOMAIN
from custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.media_source import (
    ArchivedVideoMediaSource,
    async_get_media_source,
)


def test_videos_for_day_lists_all_source_videos_newest_first(tmp_path: Path) -> None:
    storage = LocalFsStorage(tmp_path)
    folder = tmp_path / "2026" / "09"
    folder.mkdir(parents=True)
    older = folder / "camera_front_2026_09_02_08_00_00.mp4"
    newer = folder / "camera_front_2026_09_02_09_00_00.mp4"
    other_camera = folder / "camera_back_2026_09_02_10_00_00.mp4"
    previous_day = folder / "camera_front_2026_09_01_23_59_00.mp4"
    for path in (older, newer, other_camera, previous_day):
        path.write_bytes(b"video")

    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    os.utime(other_camera, (3, 3))
    os.utime(previous_day, (4, 4))

    result = storage._videos_for_day_sync(date(2026, 9, 2), "camera.front")

    assert result == [newer, older]


def test_video_for_day_rejects_other_sources_and_unsafe_paths(tmp_path: Path) -> None:
    storage = LocalFsStorage(tmp_path)
    folder = tmp_path / "2026" / "09"
    folder.mkdir(parents=True)
    video = folder / "camera_front_2026_09_02_09_00_00.mp4"
    video.write_bytes(b"video")

    assert storage._video_for_day_sync(date(2026, 9, 2), "camera.front", video.name) == video
    assert storage._video_for_day_sync(date(2026, 9, 2), "camera.back", video.name) is None
    assert storage._video_for_day_sync(date(2026, 9, 2), "camera.front", "../video.mp4") is None


def test_media_source_scans_on_browse_and_exposes_click_to_play_items(tmp_path: Path) -> None:
    async def scenario() -> None:
        older = tmp_path / "camera_front_2026_09_02_08_00_00.mp4"
        newer = tmp_path / "camera_front_2026_09_02_09_00_00.mp4"
        storage = MagicMock()
        storage.videos_for_day = AsyncMock(return_value=[newer, older])
        runtime = MagicMock(storage=storage)
        hass = MagicMock()
        hass.data = {
            DOMAIN: {
                "runtimes": {"entry-1": runtime},
                "entry_camera_entity": {"entry-1": "camera.front"},
            }
        }
        source = ArchivedVideoMediaSource(hass)

        storage.videos_for_day.assert_not_awaited()
        with patch(
            "custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.media_source.dt_util.now",
            return_value=datetime(2026, 9, 2, tzinfo=timezone.utc),
        ):
            result = await source.async_browse_media(MediaSourceItem(hass, DOMAIN, "", None))

        storage.videos_for_day.assert_awaited_once_with(date(2026, 9, 2), "camera.front")
        assert result.can_play is False
        assert result.can_expand is True
        assert [child.title for child in result.children] == [
            "2026-09-02 09:00:00 - camera.front",
            "2026-09-02 08:00:00 - camera.front",
        ]
        assert all(child.media_class == MediaClass.VIDEO for child in result.children)
        assert all(child.can_play and not child.can_expand for child in result.children)

    asyncio.run(scenario())


def test_media_source_resolves_selected_video_to_authenticated_route(tmp_path: Path) -> None:
    async def scenario() -> None:
        video = tmp_path / "camera_front_2026_09_02_09_00_00.mp4"
        storage = MagicMock()
        storage.video_for_day = AsyncMock(return_value=video)
        runtime = MagicMock(storage=storage)
        hass = MagicMock()
        hass.data = {
            DOMAIN: {
                "runtimes": {"entry-1": runtime},
                "entry_camera_entity": {"entry-1": "camera.front"},
            }
        }
        source = ArchivedVideoMediaSource(hass)
        item = MediaSourceItem(hass, DOMAIN, f"video/entry-1/{video.name}", None)

        with patch(
            "custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.media_source.dt_util.now",
            return_value=datetime(2026, 9, 2, tzinfo=timezone.utc),
        ):
            result = await source.async_resolve_media(item)

        storage.video_for_day.assert_awaited_once_with(
            date(2026, 9, 2),
            "camera.front",
            video.name,
        )
        assert result.url == f"/api/{DOMAIN}/media/entry-1/{video.name}"
        assert result.mime_type == "video/mp4"
        assert result.path == video

    asyncio.run(scenario())


def test_media_source_rejects_unknown_items() -> None:
    async def scenario() -> None:
        hass = MagicMock()
        hass.data = {DOMAIN: {"runtimes": {}, "entry_camera_entity": {}}}
        source = ArchivedVideoMediaSource(hass)

        with pytest.raises(BrowseError):
            await source.async_browse_media(MediaSourceItem(hass, DOMAIN, "video/item", None))
        with pytest.raises(Unresolvable):
            await source.async_resolve_media(
                MediaSourceItem(hass, DOMAIN, "video/entry-1/../secret.mp4", None)
            )

    asyncio.run(scenario())


def test_media_source_platform_registers_single_video_view() -> None:
    async def scenario() -> None:
        hass = MagicMock()

        source = await async_get_media_source(hass)

        assert isinstance(source, ArchivedVideoMediaSource)
        hass.http.register_view.assert_called_once()

    asyncio.run(scenario())
