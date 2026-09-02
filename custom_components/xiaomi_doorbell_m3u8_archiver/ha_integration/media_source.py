"""Media source exposing today's archived doorbell videos."""

from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import quote

from aiohttp import web

from homeassistant.components import http
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

from ..const import CONF_NAME_DEFAULT, DOMAIN
from .coordinator import IntegrationRuntime

_MEDIA_URL = f"/api/{DOMAIN}/media"
_VIDEO_IDENTIFIER = "video"
_ARCHIVE_TIMESTAMP_PATTERN = re.compile(
    r".*_(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})_(\d{2})(?:_\d+)?$"
)


async def async_get_media_source(hass: HomeAssistant) -> ArchivedVideoMediaSource:
    """Set up the integration's single media source."""
    source = ArchivedVideoMediaSource(hass)
    hass.http.register_view(ArchivedVideoView(source))
    return source


class ArchivedVideoMediaSource(MediaSource):
    """Dynamically browse archived videos without creating entities."""

    name = CONF_NAME_DEFAULT

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

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

    @staticmethod
    def _video_item(
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
        )

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


def _format_archive_timestamp(video_path: Path) -> str:
    match = _ARCHIVE_TIMESTAMP_PATTERN.fullmatch(video_path.stem)
    if match is None:
        return video_path.stem
    year, month, day, hour, minute, second = match.groups()
    return f"{year}-{month}-{day} {hour}:{minute}:{second}"
