"""Read-only archive and thumbnail capabilities, separate from ingestion."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Protocol

from ..domain.archived_video import ArchivedVideo


class ArchiveCatalogPort(Protocol):
    """Query archived footage without knowing its storage layout."""

    async def videos_for_day(self, day: date, source_ref: str) -> list[ArchivedVideo]:
        """List footage captured on the requested local calendar day."""

    async def resolve_video(self, source_ref: str, identifier: str) -> Path | None:
        """Resolve a stable identifier independently of the current date."""


class ThumbnailExtractorPort(Protocol):
    """Extract a JPEG without depending on the ingestion processor contract."""

    async def extract_thumbnail(self, video_path: Path) -> bytes:
        """Return a representative JPEG frame."""


class ThumbnailGenerationError(Exception):
    """A present resource could not be converted to a thumbnail."""


class ThumbnailProviderPort(Protocol):
    """Provide thumbnails without exposing cache or extraction details."""

    async def get(self, video_path: Path) -> bytes:
        """Return JPEG bytes or raise a missing-file or generation error."""
