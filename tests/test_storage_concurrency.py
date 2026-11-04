"""Source namespaces, legacy ownership and exclusive archive writes."""

import asyncio
from datetime import date
from pathlib import Path
import shutil
from threading import Barrier, Event
from typing import Any, BinaryIO
from unittest.mock import patch
from urllib.parse import quote

import pytest

from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.storage.local_fs_storage import (
    LocalFsStorage,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.core.domain import Artifact, Job


async def inline_io(func: Any, *args: Any, **kwargs: Any) -> Any:
    return func(*args, **kwargs)


@pytest.mark.parametrize(
    "sources",
    [("camera.front_door", "camera.front__door"), ("camera.demo", "camera.demo")],
)
def test_concurrent_stores_never_overwrite(tmp_path: Path, sources: tuple[str, str]) -> None:
    async def scenario() -> None:
        stores = [LocalFsStorage(tmp_path / "archive") for _ in sources]
        inputs = [tmp_path / f"{index}.mp4" for index in range(2)]
        for index, path in enumerate(inputs):
            path.write_bytes(f"video-{index}".encode())
        barrier = Barrier(2, timeout=3)
        copy = shutil.copyfileobj

        def concurrent_copy(source: BinaryIO, target: BinaryIO) -> None:
            barrier.wait()
            copy(source, target)

        with patch("shutil.copyfileobj", new=concurrent_copy):
            records = await asyncio.gather(
                *(
                    store.store(
                        Job(
                            str(index),
                            source,
                            metadata={"motion_video_time": "2026-10-19T10:00:00Z"},
                        ),
                        Artifact("output", str(path)),
                    )
                    for index, (store, source, path) in enumerate(zip(stores, sources, inputs))
                )
            )
        assert records[0].location != records[1].location
        assert [Path(record.location).read_bytes() for record in records] == [
            b"video-0",
            b"video-1",
        ]
        assert all(not path.exists() for path in inputs)
        if sources[0] != sources[1]:
            for index, (store, source) in enumerate(zip(stores, sources)):
                videos = await store.videos_for_day(date(2026, 10, 19), source)
                assert len(videos) == 1
                assert videos[0].path == Path(records[index].location)
                other = Path(records[1 - index].location).name
                assert await store.resolve_video(source, other) is None

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_legacy_files_require_an_unambiguous_configured_owner(tmp_path: Path) -> None:
    async def scenario() -> None:
        configured = {"first": "camera.front_door"}
        storage = LocalFsStorage(tmp_path, legacy_sources=configured.values())
        folder = tmp_path / "2026" / "10"
        folder.mkdir(parents=True)
        legacy = folder / "camera_front_door_2026_10_19_10_00_00.mp4"
        legacy.write_bytes(b"legacy")
        with patch("asyncio.to_thread", new=inline_io):
            assert await storage.resolve_video("camera.front_door", legacy.name) == legacy
            configured["second"] = "camera.front__door"
            for source in configured.values():
                assert await storage.resolve_video(source, legacy.name) is None
                assert await storage.videos_for_day(date(2026, 10, 19), source) == []
        assert legacy.read_bytes() == b"legacy"

    asyncio.run(scenario())


def test_failed_copy_removes_only_its_own_destination(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = LocalFsStorage(tmp_path / "archive")
        folder = tmp_path / "archive" / "2026" / "10"
        folder.mkdir(parents=True)
        existing = folder / "camera.demo_2026_10_19_10_00_00.mp4"
        existing.write_bytes(b"existing")
        source = tmp_path / "input.mp4"
        source.write_bytes(b"input")

        def fail_copy(src: BinaryIO, target: BinaryIO) -> None:
            target.write(b"partial")
            raise OSError("copy failed")

        with patch("shutil.copyfileobj", new=fail_copy), patch("asyncio.to_thread", new=inline_io):
            with pytest.raises(OSError, match="copy failed"):
                await storage.store(
                    Job(
                        "job", "camera.demo", metadata={"motion_video_time": "2026-10-19T10:00:00Z"}
                    ),
                    Artifact("output", str(source)),
                )
        assert list(folder.iterdir()) == [existing]
        assert existing.read_bytes() == b"existing"
        assert source.read_bytes() == b"input"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "source", ["camera.front_door", "camera.front__door", "camera.a-b", "摄像头/门铃"]
)
def test_new_source_prefix_preserves_identity(tmp_path: Path, source: str) -> None:
    async def scenario() -> None:
        storage = LocalFsStorage(tmp_path / "archive")
        path = tmp_path / "input.mp4"
        path.write_bytes(b"video")
        with patch("asyncio.to_thread", new=inline_io):
            record = await storage.store(
                Job("job", source, metadata={"motion_video_time": "2026-10-19T10:00:00Z"}),
                Artifact("output", str(path)),
            )
            saved = Path(record.location)
            assert saved.name.startswith(quote(source, safe="") + "_")
            assert await storage.resolve_video(source, saved.name) == saved

    asyncio.run(scenario())


def test_incomplete_copies_and_empty_reservations_are_not_browsable(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = LocalFsStorage(tmp_path / "archive")
        source = tmp_path / "input.mp4"
        source.write_bytes(b"video")
        copying, release = Event(), Event()

        def slow_copy(src: BinaryIO, target: BinaryIO) -> None:
            target.write(b"partial")
            copying.set()
            if not release.wait(timeout=3):
                raise TimeoutError("test did not release copy")
            target.seek(0)
            target.truncate()
            target.write(src.read())

        with patch("shutil.copyfileobj", new=slow_copy):
            pending = asyncio.create_task(
                storage.store(
                    Job(
                        "job", "camera.demo", metadata={"motion_video_time": "2026-10-19T10:00:00Z"}
                    ),
                    Artifact("output", str(source)),
                )
            )
            try:
                assert await asyncio.to_thread(copying.wait, 2)
                folder = tmp_path / "archive" / "2026" / "10"
                reservation = storage._reserve_destination(
                    folder, "camera.demo_2026_10_19_10_00_00"
                )
                assert await storage.videos_for_day(date(2026, 10, 19), "camera.demo") == []
                assert await storage.resolve_video("camera.demo", reservation.name) is None
            finally:
                release.set()
            record = await pending
        assert Path(record.location).read_bytes() == b"video"
        assert len(await storage.videos_for_day(date(2026, 10, 19), "camera.demo")) == 1

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_failed_publish_removes_reservation_and_staging_files(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = LocalFsStorage(tmp_path / "archive")
        source = tmp_path / "input.mp4"
        source.write_bytes(b"input")
        with (
            patch.object(Path, "replace", side_effect=OSError("publish failed")),
            patch("asyncio.to_thread", new=inline_io),
        ):
            with pytest.raises(OSError, match="publish failed"):
                await storage.store(
                    Job(
                        "job", "camera.demo", metadata={"motion_video_time": "2026-10-19T10:00:00Z"}
                    ),
                    Artifact("output", str(source)),
                )
        assert list((tmp_path / "archive" / "2026" / "10").iterdir()) == []
        assert source.read_bytes() == b"input"

    asyncio.run(scenario())
