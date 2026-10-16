"""Local filesystem storage adapter."""

from __future__ import annotations

import asyncio
import re
import shutil
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path

from ...const import META_MOTION_VIDEO_TIME
from ...core.domain import Artifact, Job, StorageRecord
from ...core.domain.archived_video import ArchivedVideo
from ...core.policies import RetentionPolicy


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LocalFsStorage:
    """Store processed output into date-based local storage path."""

    def __init__(self, storage_root: Path, local_timezone: tzinfo = timezone.utc) -> None:
        self._storage_root = storage_root
        self._local_timezone = local_timezone
        self._storage_root.mkdir(parents=True, exist_ok=True)

    async def store(self, job: Job, output_artifact: Artifact) -> StorageRecord:
        src = Path(output_artifact.uri_or_path)
        if not src.exists():
            raise FileNotFoundError(f"Output artifact not found: {src}")

        event_dt = self._extract_event_time(job)
        # Year/month partitioning keeps long-term storage navigable and avoids huge
        # flat directories that degrade filesystem performance.
        subdir = self._storage_root / event_dt.strftime("%Y") / event_dt.strftime("%m")
        await asyncio.to_thread(subdir.mkdir, parents=True, exist_ok=True)

        source_prefix = self._sanitize_source_ref(job.source_ref)
        base_name = f"{source_prefix}_{event_dt.strftime('%Y_%m_%d_%H_%M_%S')}"
        dst = self._build_non_conflicting_path(subdir, base_name)

        await asyncio.to_thread(shutil.move, str(src), str(dst))
        return StorageRecord(location=str(dst), meta={"job_id": job.job_id})

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

        source_prefix = self._sanitize_source_ref(source_ref)
        date_prefix = day.strftime("%Y_%m_%d")
        candidates: list[ArchivedVideo] = []
        for path in folder.glob(f"{source_prefix}_{date_prefix}_*.mp4"):
            try:
                captured_at = self._capture_time(source_ref, path.name)
                if captured_at is None or captured_at.date() != day:
                    continue
                video_path = self._resolve_video_sync(source_ref, path.name)
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
        if Path(filename).name != filename:
            return None

        captured_at = self._capture_time(source_ref, filename)
        if captured_at is None:
            return None

        candidate = self._storage_root / captured_at.strftime("%Y/%m") / filename
        if not candidate.resolve().is_relative_to(self._storage_root.resolve()):
            return None
        return candidate if candidate.is_file() else None

    def _capture_time(self, source_ref: str, filename: str) -> datetime | None:
        prefix = re.escape(self._sanitize_source_ref(source_ref))
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

    def _build_non_conflicting_path(self, folder: Path, base_name: str) -> Path:
        candidate = folder / f"{base_name}.mp4"
        if not candidate.exists():
            return candidate

        suffix = 1
        while True:
            candidate = folder / f"{base_name}_{suffix:02d}.mp4"
            if not candidate.exists():
                return candidate
            suffix += 1

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

    def _sanitize_source_ref(self, source_ref: str) -> str:
        # Source refs are entity-like ids (for example camera.xxx); sanitize to keep
        # filenames portable across filesystems while preserving human readability.
        value = re.sub(r"[^A-Za-z0-9_-]+", "_", source_ref.strip())
        value = re.sub(r"_+", "_", value).strip("_")
        return value or "unknown_source"
