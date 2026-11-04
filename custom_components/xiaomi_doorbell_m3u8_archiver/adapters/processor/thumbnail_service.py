"""Bounded thumbnail generation and caching independent of HA transports."""

from __future__ import annotations

import asyncio
from pathlib import Path
from weakref import WeakValueDictionary

from ...core.ports.media_port import ThumbnailExtractorPort, ThumbnailGenerationError


class ThumbnailService:
    """Deduplicate requests and limit concurrent thumbnail extraction."""

    def __init__(
        self, extractor: ThumbnailExtractorPort, *, slots: asyncio.Semaphore | None = None
    ) -> None:
        self._extractor = extractor
        self._cache: dict[Path, tuple[int, bytes]] = {}
        self._locks: WeakValueDictionary[Path, asyncio.Lock] = WeakValueDictionary()
        self._slots = slots if slots is not None else asyncio.Semaphore(2)

    async def get(self, video_path: Path) -> bytes:
        """Return a thumbnail; preserve missing-file errors for the caller."""
        try:
            modified_ns = (await asyncio.to_thread(video_path.stat)).st_mtime_ns
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise ThumbnailGenerationError(str(exc)) from exc

        try:
            lock = self._locks.setdefault(video_path, asyncio.Lock())
            async with lock:
                cached = self._cache.get(video_path)
                if cached is not None and cached[0] == modified_ns:
                    return cached[1]
                async with self._slots:
                    thumbnail = await self._extractor.extract_thumbnail(video_path)
                if not thumbnail:
                    raise ThumbnailGenerationError("Thumbnail extractor returned no image")
                self._cache.pop(video_path, None)
                if len(self._cache) >= 256:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[video_path] = (modified_ns, thumbnail)
                return thumbnail
        except (OSError, RuntimeError) as exc:
            raise ThumbnailGenerationError(str(exc)) from exc
