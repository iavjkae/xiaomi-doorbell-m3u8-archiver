"""Media source exposing today's archived doorbell videos."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from pathlib import Path
import re
from urllib.parse import quote
from weakref import WeakValueDictionary

from aiohttp import web

from homeassistant.components import http
from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.media_player import BrowseError, MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from .coordinator import IntegrationRuntime

_MEDIA_URL = f"/api/{DOMAIN}/media"
_THUMBNAIL_URL = f"/api/{DOMAIN}/thumbnail"
_VIDEO_IDENTIFIER = "video"
_THUMBNAIL_EXPIRY = timedelta(hours=24)
_THUMBNAIL_CACHE_LIMIT = 256
_ARCHIVE_TIMESTAMP_PATTERN = re.compile(
    r".*_(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})_(\d{2})(?:_\d+)?$"
)

_LOGGER = logging.getLogger(__name__)


async def async_get_media_source(hass: HomeAssistant) -> ArchivedVideoMediaSource:
    """Set up the integration's single media source."""
    source = ArchivedVideoMediaSource(hass)
    hass.http.register_view(ArchivedVideoView(source))
    hass.http.register_view(ArchivedVideoThumbnailView(source))
    return source


class ArchivedVideoMediaSource(MediaSource):
    """Dynamically browse archived videos without creating entities."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass
        self.name = _media_source_name(hass)
        self._thumbnail_cache: dict[tuple[Path, int], bytes] = {}
        self._thumbnail_locks: WeakValueDictionary[Path, asyncio.Lock] = WeakValueDictionary()
        self._thumbnail_slots = asyncio.Semaphore(2)

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Return all videos archived today when the source is opened."""
        if item.identifier:
            raise BrowseError(f"Media item is not browsable: {item.identifier}")

        children: list[BrowseMediaSource] = []
        for entry_id, runtime, camera_entity_id in self._configured_runtimes():
            video_paths = await runtime.storage.videos_for_day(
                dt_util.now().date(),
                camera_entity_id,
            )
            children.extend(
                self._video_item(entry_id, camera_entity_id, path) for path in video_paths
            )

        children.sort(key=lambda child: child.title, reverse=True)
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=None,
            media_class=MediaClass.APP,
            media_content_type=MediaType.VIDEO,
            title=self.name,
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.VIDEO,
        )

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a selected media item to an authenticated HA URL."""
        entry_id, filename = self._parse_video_identifier(item.identifier)
        video_path = await self.async_resolve_video(entry_id, filename)
        if video_path is None:
            raise Unresolvable(f"Archived video is no longer available: {filename}")

        encoded_entry = quote(entry_id, safe="")
        encoded_filename = quote(filename, safe="")
        return PlayMedia(
            f"{_MEDIA_URL}/{encoded_entry}/{encoded_filename}",
            "video/mp4",
            path=video_path,
        )

    async def async_resolve_video(self, entry_id: str, filename: str) -> Path | None:
        """Resolve a current-day video for media playback."""
        domain_data = self.hass.data.get(DOMAIN, {})
        runtime: IntegrationRuntime | None = domain_data.get("runtimes", {}).get(entry_id)
        camera_entity_id = domain_data.get("entry_camera_entity", {}).get(entry_id)
        if runtime is None or not camera_entity_id:
            return None
        return await runtime.storage.video_for_day(
            dt_util.now().date(),
            camera_entity_id,
            filename,
        )

    def _configured_runtimes(self) -> list[tuple[str, IntegrationRuntime, str]]:
        domain_data = self.hass.data.get(DOMAIN, {})
        runtimes: dict[str, IntegrationRuntime] = domain_data.get("runtimes", {})
        entry_cameras: dict[str, str] = domain_data.get("entry_camera_entity", {})
        return [
            (entry_id, runtime, entry_cameras[entry_id])
            for entry_id, runtime in runtimes.items()
            if entry_id in entry_cameras
        ]

    def _video_item(
        self,
        entry_id: str,
        camera_entity_id: str,
        video_path: Path,
    ) -> BrowseMediaSource:
        captured_at = _format_archive_timestamp(video_path)
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{_VIDEO_IDENTIFIER}/{entry_id}/{video_path.name}",
            media_class=MediaClass.VIDEO,
            media_content_type="video/mp4",
            title=f"{captured_at} - {camera_entity_id}",
            can_play=True,
            can_expand=False,
            thumbnail=self._thumbnail_url(entry_id, video_path.name),
        )

    def _thumbnail_url(self, entry_id: str, filename: str) -> str:
        encoded_entry = quote(entry_id, safe="")
        encoded_filename = quote(filename, safe="")
        path = f"{_THUMBNAIL_URL}/{encoded_entry}/{encoded_filename}"
        return async_sign_path(self.hass, path, _THUMBNAIL_EXPIRY)

    async def async_thumbnail(self, entry_id: str, filename: str) -> bytes | None:
        """Return a cached representative frame for an archived video."""
        video_path = await self.async_resolve_video(entry_id, filename)
        if video_path is None:
            return None

        try:
            modified_ns = (await asyncio.to_thread(video_path.stat)).st_mtime_ns
        except OSError:
            return None
        cache_key = (video_path, modified_ns)
        if cached := self._thumbnail_cache.get(cache_key):
            return cached

        lock = self._thumbnail_locks.setdefault(video_path, asyncio.Lock())
        async with lock:
            if cached := self._thumbnail_cache.get(cache_key):
                return cached
            runtime = self._runtime(entry_id)
            if runtime is None:
                return None
            async with self._thumbnail_slots:
                thumbnail = await runtime.orchestrator.processor.extract_thumbnail(video_path)
            stale_keys = [
                key for key in self._thumbnail_cache if key[0] == video_path and key != cache_key
            ]
            for stale_key in stale_keys:
                self._thumbnail_cache.pop(stale_key, None)
            if len(self._thumbnail_cache) >= _THUMBNAIL_CACHE_LIMIT:
                self._thumbnail_cache.pop(next(iter(self._thumbnail_cache)))
            self._thumbnail_cache[cache_key] = thumbnail
            return thumbnail

    def _runtime(self, entry_id: str) -> IntegrationRuntime | None:
        domain_data = self.hass.data.get(DOMAIN, {})
        return domain_data.get("runtimes", {}).get(entry_id)

    @staticmethod
    def _parse_video_identifier(identifier: str) -> tuple[str, str]:
        parts = identifier.split("/")
        if len(parts) != 3 or parts[0] != _VIDEO_IDENTIFIER:
            raise Unresolvable(f"Unknown media item: {identifier}")
        _, entry_id, filename = parts
        if not entry_id or Path(filename).name != filename:
            raise Unresolvable(f"Invalid media item: {identifier}")
        return entry_id, filename


class ArchivedVideoView(http.HomeAssistantView):
    """Serve archived videos through Home Assistant's signed URL authentication."""

    url = f"{_MEDIA_URL}/{{entry_id}}/{{filename}}"
    name = f"api:{DOMAIN}:media"
    requires_auth = True

    def __init__(self, source: ArchivedVideoMediaSource) -> None:
        self._source = source

    async def head(
        self,
        request: web.Request,
        entry_id: str,
        filename: str,
    ) -> web.Response:
        """Validate a video before playback."""
        await self._video_path(entry_id, filename)
        return web.Response()

    async def get(
        self,
        request: web.Request,
        entry_id: str,
        filename: str,
    ) -> web.FileResponse:
        """Stream an archived MP4 with byte-range support."""
        return web.FileResponse(await self._video_path(entry_id, filename))

    async def _video_path(self, entry_id: str, filename: str) -> Path:
        path = await self._source.async_resolve_video(entry_id, filename)
        if path is None:
            raise web.HTTPNotFound
        return path


class ArchivedVideoThumbnailView(http.HomeAssistantView):
    """Serve signed, lazily generated video thumbnails."""

    url = f"{_THUMBNAIL_URL}/{{entry_id}}/{{filename}}"
    name = f"api:{DOMAIN}:thumbnail"
    requires_auth = True

    def __init__(self, source: ArchivedVideoMediaSource) -> None:
        self._source = source

    async def get(
        self,
        request: web.Request,
        entry_id: str,
        filename: str,
    ) -> web.Response:
        """Return a representative JPEG frame for one video."""
        try:
            thumbnail = await self._source.async_thumbnail(entry_id, filename)
        except (OSError, RuntimeError):
            _LOGGER.exception("Failed to generate thumbnail for %s", filename)
            raise web.HTTPNotFound from None
        if thumbnail is None:
            raise web.HTTPNotFound
        return web.Response(
            body=thumbnail,
            content_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=3600"},
        )


def _format_archive_timestamp(video_path: Path) -> str:
    match = _ARCHIVE_TIMESTAMP_PATTERN.fullmatch(video_path.stem)
    if match is None:
        return video_path.stem
    year, month, day, hour, minute, second = match.groups()
    return f"{year}-{month}-{day} {hour}:{minute}:{second}"


def _media_source_name(hass: HomeAssistant) -> str:
    language = getattr(getattr(hass, "config", None), "language", "en")
    if not isinstance(language, str):
        return "Today's Doorbell Videos"
    normalized = language.lower()
    if normalized in {"zh-hant", "zh-tw"}:
        return "今日門鈴影片"
    if normalized.startswith("zh"):
        return "今日门铃视频"
    return "Today's Doorbell Videos"
