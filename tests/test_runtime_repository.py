"""Bounded job history and independent runtime counters."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.repository.runtime_repository import (
    RuntimeRepository,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.core.domain import Job, JobStatus


@pytest.mark.parametrize("history_limit", [0, 2])
def test_history_evicts_only_terminal_jobs_and_preserves_counters(history_limit: int) -> None:
    async def scenario() -> None:
        repository = RuntimeRepository(history_limit=history_limit)
        active = Job("active", "camera.demo")
        await repository.save(active)
        for index in range(10):
            job = Job(str(index), "camera.demo")
            await repository.save(job)
            job.transition(JobStatus.DONE if index % 2 else JobStatus.FAILED)
            await repository.update(job)
        assert len(repository._jobs) == len(repository._status_snapshot) == history_limit + 1
        assert len(repository._completed_ids) == history_limit
        assert await repository.get("active") is active
        assert repository.total_done == repository.total_failed == 5
        assert repository.processed_today == 5
        if history_limit:
            retained = await repository.get("9")
            await repository.update(retained)
            assert repository.total_done == 5
            assert len(repository._completed_ids) == history_limit

    asyncio.run(scenario())


def test_daily_metrics_roll_over_on_read_in_configured_timezone() -> None:
    async def scenario() -> None:
        local_timezone = timezone(timedelta(hours=8))
        with patch(
            "custom_components.xiaomi_doorbell_m3u8_archiver.adapters.repository.runtime_repository.datetime"
        ) as clock:
            clock.now.return_value = datetime(2026, 10, 16, 23, 59, tzinfo=local_timezone)
            repository = RuntimeRepository(local_timezone=local_timezone)
            job = Job("done", "camera.demo")
            await repository.save(job)
            job.transition(JobStatus.DONE)
            await repository.update(job)
            assert repository.processed_today == 1
            clock.now.return_value += timedelta(minutes=2)
            assert repository.metrics()["processed_today"] == 0
            assert repository.total_done == 1
            clock.now.assert_called_with(local_timezone)

    asyncio.run(scenario())


def test_history_limit_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="history_limit"):
        RuntimeRepository(history_limit=-1)
