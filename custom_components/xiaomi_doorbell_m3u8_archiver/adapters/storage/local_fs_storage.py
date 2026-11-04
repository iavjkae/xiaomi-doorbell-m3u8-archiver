"""Local filesystem storage adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from contextlib import suppress
import logging
import re
import shutil
import stat
import tempfile
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from urllib.parse import quote

from ...const import META_MOTION_VIDEO_TIME
from ...core.domain import Artifact, Job, StorageRecord
from ...core.domain.archived_video import ArchivedVideo
from ...core.policies import RetentionPolicy

_LOGGER = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LocalFsStorage:
    """Store processed output into date-based local storage path."""

    def __init__(
        self,
        storage_root: Path,
        local_timezone: tzinfo = timezone.utc,
        *,
        legacy_sources: Collection[str] = (),
    ) -> None:
        self._storage_root = storage_root
        self._local_timezone = local_timezone
        self._legacy_sources = legacy_sources
        self._storage_root.mkdir(parents=True, exist_ok=True)

    async def store(self, job: Job, output_artifact: Artifact) -> StorageRecord:
        event_dt = self._extract_event_time(job)
        subdir = self._storage_root / event_dt.strftime("%Y") / event_dt.strftime("%m")
        source_prefix = quote(job.source_ref, safe="")
        base_name = f"{source_prefix}_{event_dt.strftime('%Y_%m_%d_%H_%M_%S')}"
        dst = await asyncio.to_thread(
            self._store_sync, Path(output_artifact.uri_or_path), subdir, base_name
        )
        return StorageRecord(location=str(dst), meta={"job_id": job.job_id})

    def _store_sync(self, src: Path, folder: Path, base_name: str) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            dir=folder, prefix=".archive-", ignore_cleanup_errors=True
        ) as staging:
            staged = Path(staging) / "video.tmp"
            with src.open("rb") as source, staged.open("wb") as target:
                shutil.copyfileobj(source, target)
            shutil.copystat(src, staged)
            dst = self._reserve_destination(folder, base_name)
            try:
                staged.replace(dst)
            except BaseException:
                with suppress(OSError):
                    dst.unlink(missing_ok=True)
                raise
        try:
            src.unlink()
        except OSError:
            _LOGGER.warning("Archived %s but could not remove source %s", dst, src, exc_info=True)
        return dst

    def _reserve_destination(self, folder: Path, base_name: str) -> Path:
        """Reserve a unique name; readers ignore the empty reservation."""
        suffix = 0
        while True:
            name = f"{base_name}_{suffix:02d}" if suffix else base_name
            dst = folder / f"{name}.mp4"
            try:
                dst.touch(exist_ok=False)
                return dst
            except FileExistsError:
                suffix += 1

    async def videos_for_day(self, day: date, source_ref: str) -> list[ArchivedVideo]:
        """Return archive metadata for one source on a local calendar day."""
        return await asyncio.to_thread(self._videos_for_day_sync, day, source_ref)

    async def resolve_video(
        self,
        source_ref: str,
        identifier: str,
    ) -> Path | None:
        """Resolve one archived video without allowing paths outside storage."""
        return await asyncio.to_thread(
            self._resolve_video_sync,
            source_ref,
            identifier,
        )

    def _videos_for_day_sync(self, day: date, source_ref: str) -> list[ArchivedVideo]:
        folder = self._storage_root / day.strftime("%Y") / day.strftime("%m")
        if not folder.is_dir():
            return []

        date_prefix = day.strftime("%Y_%m_%d")
        candidates: list[ArchivedVideo] = []
        for path in folder.glob(f"*_{date_prefix}_*.mp4"):
            try:
                captured_at = self._capture_time(source_ref, path.name)
                if captured_at is None or captured_at.date() != day:
                    continue
                video_path = self._validated_video_path(path.name, captured_at)
                if video_path is not None:
                    candidates.append(ArchivedVideo(path.name, captured_at, video_path))
            except OSError:
                continue
        return candidates

    def _resolve_video_sync(
        self,
        source_ref: str,
        filename: str,
    ) -> Path | None:
        captured_at = self._capture_time(source_ref, filename)
        if captured_at is None:
            return None
        return self._validated_video_path(filename, captured_at)

    def _validated_video_path(self, filename: str, captured_at: datetime) -> Path | None:
        if Path(filename).name != filename:
            return None

        candidate = self._storage_root / captured_at.strftime("%Y/%m") / filename
        if not candidate.resolve().is_relative_to(self._storage_root.resolve()):
            return None
        try:
            info = candidate.stat()
        except FileNotFoundError:
            return None
        return candidate if stat.S_ISREG(info.st_mode) and info.st_size > 0 else None

    def _capture_time(self, source_ref: str, filename: str) -> datetime | None:
        prefixes = self._source_prefixes(source_ref)
        prefix = "(?:" + "|".join(re.escape(value) for value in prefixes) + ")"
        match = re.fullmatch(
            rf"{prefix}_(\d{{4}}_\d{{2}}_\d{{2}}_\d{{2}}_\d{{2}}_\d{{2}})(?:_\d+)?\.mp4",
            filename,
        )
        if match is None:
            return None
        try:
            return datetime.strptime(match[1], "%Y_%m_%d_%H_%M_%S").replace(
                tzinfo=self._local_timezone
            )
        except ValueError:
            return None

    def _source_prefixes(self, source_ref: str) -> list[str]:
        """Read legacy names only when configured sources give them one owner."""
        encoded = quote(source_ref, safe="")
        legacy = self._legacy_source_prefix(source_ref)
        conflicting_source = any(
            other != source_ref and self._legacy_source_prefix(other) == legacy
            for other in self._legacy_sources
        )
        if legacy != encoded and not conflicting_source:
            return [encoded, legacy]
        return [encoded]

    async def apply_policy(self, policy: RetentionPolicy) -> dict:
        if not policy.delete_enabled:
            return {
                "deleted": 0,
                "delete_enabled": False,
                "retention_days": policy.retention_days,
                "active_keep_count": policy.active_keep_count,
            }
        return await asyncio.to_thread(self._apply_policy_sync, policy)

    def _apply_policy_sync(self, policy: RetentionPolicy) -> dict:
        deleted = 0
        files = sorted(
            self._storage_root.rglob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        now = _utcnow()
        keep_set = set(files[: policy.active_keep_count]) if policy.active_keep_count > 0 else set()
        cutoff = now - timedelta(days=policy.retention_days)

        for path in files:
            # Keep-count has priority over age cutoff so newest footage is retained
            # even when system clocks or source timestamps are imperfect.
            if path in keep_set:
                continue
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                path.unlink(missing_ok=True)
                deleted += 1

        return {
            "deleted": deleted,
            "delete_enabled": True,
            "retention_days": policy.retention_days,
            "active_keep_count": policy.active_keep_count,
        }

    def _extract_event_time(self, job: Job) -> datetime:
        raw = str(job.metadata.get(META_MOTION_VIDEO_TIME, "")).strip()
        if raw:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                pass
            else:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=self._local_timezone)
                return parsed.astimezone(self._local_timezone)
        return datetime.now(self._local_timezone)

    def _legacy_source_prefix(self, source_ref: str) -> str:
        value = re.sub(r"[^A-Za-z0-9_-]+", "_", source_ref.strip())
        value = re.sub(r"_+", "_", value).strip("_")
        return value or "unknown_source"
