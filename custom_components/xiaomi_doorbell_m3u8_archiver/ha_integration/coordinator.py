"""Runtime coordinator for Home Assistant integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from homeassistant.util import dt as dt_util

from ..adapters.storage.local_fs_storage import LocalFsStorage
from ..adapters.processor.ffmpeg_processor import FfmpegProcessor
from ..adapters.repository.runtime_repository import RuntimeRepository
from ..adapters.source.http_m3u8_source import HttpM3U8Source
from ..const import (
    CONF_FFMPEG_PATH,
    CONF_STORAGE_ROOT,
    DOMAIN,
    META_MOTION_VIDEO_TIME,
    SENSOR_IS_BUSY,
)
from ..core.domain import Job, JobStatus, SourceRequest
from ..core.orchestrator import Orchestrator
from ..core.policies import QueuePolicy, RetentionPolicy

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
# Per-entry key avoids cross-entry overwrite when multiple runtimes persist
# motion cursor updates concurrently.
STORAGE_KEY_PREFIX = f"{DOMAIN}_state"

REQUIRED_CONFIG_KEYS = (
    "fetch_concurrency",
    "process_concurrency",
    "max_queue_size",
    "retry_limit",
    "retry_backoff_sec",
    CONF_FFMPEG_PATH,
    CONF_STORAGE_ROOT,
)


@dataclass(slots=True)
class IntegrationRuntime:
    """Encapsulate running orchestrator and adapters.

    Runtime owns event-level deduplication cursor for HA events only; manual service
    calls intentionally bypass cursor updates to preserve operator control.
    """

    orchestrator: Orchestrator
    repository: Any
    storage: Any
    entry_id: str = "default"
    hass: Any = None
    unsub_listener: Any = None
    last_motion_by_entity: dict[str, str] = field(default_factory=dict)
    _motion_state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _storage: Any = None

    @classmethod
    async def async_from_config(
        cls,
        hass: Any,
        entry_id: str,
        cfg: dict[str, Any],
    ) -> "IntegrationRuntime":
        """Build runtime from integration config."""
        _LOGGER.debug(
            "Building runtime for entry %s with config keys: %s", entry_id, sorted(cfg.keys())
        )
        missing = [key for key in REQUIRED_CONFIG_KEYS if key not in cfg]
        if missing:
            missing_joined = ", ".join(missing)
            raise ValueError(f"Missing config keys for entry {entry_id}: {missing_joined}")

        fetch_concurrency = int(cfg["fetch_concurrency"])
        process_concurrency = int(cfg["process_concurrency"])
        max_queue_size = int(cfg["max_queue_size"])
        retry_limit = int(cfg["retry_limit"])
        retry_backoff_sec = float(cfg["retry_backoff_sec"])

        queue_policy = QueuePolicy(
            fetch_concurrency=fetch_concurrency,
            process_concurrency=process_concurrency,
            max_queue_size=max_queue_size,
            retry_limit=retry_limit,
            retry_backoff_sec=retry_backoff_sec,
        )
        retention_policy = RetentionPolicy()
        source = HttpM3U8Source()
        processor = FfmpegProcessor(ffmpeg_path=cfg[CONF_FFMPEG_PATH])
        configured_timezone = (
            dt_util.get_time_zone(getattr(getattr(hass, "config", None), "time_zone", "UTC"))
            or dt_util.UTC
        )
        storage = LocalFsStorage(
            storage_root=Path(cfg[CONF_STORAGE_ROOT]),
            local_timezone=configured_timezone,
        )
        repository = RuntimeRepository()
        orchestrator = Orchestrator(
            source=source,
            processor=processor,
            storage=storage,
            repository=repository,
            queue_policy=queue_policy,
            retention_policy=retention_policy,
        )
        runtime = cls(
            orchestrator=orchestrator,
            entry_id=entry_id,
            repository=repository,
            storage=storage,
            hass=hass,
        )
        await runtime._load_motion_state()
        return runtime

    async def start(self) -> None:
        """Start orchestrator workers."""
        await self.orchestrator.start()

    async def stop(self) -> None:
        """Stop listener and orchestrator workers."""
        if self.unsub_listener:
            self.unsub_listener()
            self.unsub_listener = None
        await self.orchestrator.stop()

    async def enqueue_request(self, request: SourceRequest) -> Job | None:
        """Enqueue a source request."""
        # Manual calls are explicit operator intent and should not mutate or be
        # blocked by motion cursor state.
        if request.source_type != "ha_event":
            return await self.orchestrator.enqueue(request)

        motion_time = str(request.payload.get(META_MOTION_VIDEO_TIME, "")).strip()
        if not motion_time:
            return await self.orchestrator.enqueue(request)

        async with self._motion_state_lock:
            last_motion = self.last_motion_by_entity.get(request.source_ref)
            if last_motion == motion_time:
                # Restart-safe replay suppression for HA event stream.
                return None

            job = await self.orchestrator.enqueue(request)
            if job is None:
                return None

            self.last_motion_by_entity[request.source_ref] = motion_time
            await self._save_motion_state()
            return job

    async def _load_motion_state(self) -> None:
        if self.hass is None:
            return

        try:
            from homeassistant.helpers.storage import Store  # type: ignore[import-not-found]
        except ModuleNotFoundError:
            return

        storage_key = f"{STORAGE_KEY_PREFIX}_{self.entry_id}"
        self._storage = Store(self.hass, STORAGE_VERSION, storage_key)
        data = await self._storage.async_load() or {}
        loaded = data.get("last_motion_by_entity", {})
        if isinstance(loaded, dict):
            self.last_motion_by_entity = {
                str(entity): str(motion)
                for entity, motion in loaded.items()
                if str(entity).strip() and str(motion).strip()
            }

    async def _save_motion_state(self) -> None:
        if self._storage is None:
            return
        await self._storage.async_save({"last_motion_by_entity": self.last_motion_by_entity})

    def metrics(self) -> dict[str, Any]:
        """Return in-memory metrics for sensor entities."""
        fetch_size = self.orchestrator.fetch_queue.qsize()
        process_size = self.orchestrator.process_queue.qsize()
        last_status = self.repository.last_job_status
        busy_statuses = {
            JobStatus.FETCHING.value,
            JobStatus.FETCHED.value,
            JobStatus.PROCESSING.value,
            JobStatus.PROCESSED.value,
            JobStatus.STORED.value,
        }
        # Transitional states are treated as busy so dashboards do not flap to idle
        # between storage write and final completion bookkeeping.
        busy = fetch_size > 0 or process_size > 0 or last_status in busy_statuses

        return {
            "total_done": self.repository.total_done,
            "total_failed": self.repository.total_failed,
            "processed_today": self.repository.processed_today,
            "queue_fetch_size": fetch_size,
            "queue_process_size": process_size,
            "last_job_status": last_status,
            "current_job_status": last_status,
            SENSOR_IS_BUSY: busy,
        }
