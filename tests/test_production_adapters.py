import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

import pytest

from custom_components.xiaomi_doorbell_m3u8_archiver.adapters.production import (
    FfmpegProcessor,
    HttpM3U8Source,
    LocalFsStorage,
    ProcessResult,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.const import (
    META_MOTION_VIDEO_TIME,
    META_STREAM_URL,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.core.domain import Artifact, Job, SourceRequest
from custom_components.xiaomi_doorbell_m3u8_archiver.core.policies import RetentionPolicy


def test_http_m3u8_source_downloads_playlist_and_segments(tmp_path: Path) -> None:
    # Intent: downloader must localize remote playlist dependencies for ffmpeg.
    # Expect: playlist/key/segments are rewritten and stored under job temp dir.
    async def scenario() -> None:
        base_url = "https://example.test/path/clip.m3u8"
        fixtures = {
            base_url: (
                b"#EXTM3U\n"
                b'#EXT-X-KEY:METHOD=AES-128,URI="https://example.test/path/key"\n'
                b"#EXTINF:1,\n"
                b"seg1.ts\n"
                b"#EXTINF:1,\n"
                b"sub/seg2.ts\n"
                b"#EXT-X-ENDLIST\n"
            ),
            "https://example.test/path/key": b"secret-key",
            "https://example.test/path/seg1.ts": b"segment-one",
            "https://example.test/path/sub/seg2.ts": b"segment-two",
        }

        source = HttpM3U8Source(temp_root=tmp_path, fetch_bytes=lambda url: fixtures[url])
        req = SourceRequest(
            source_type="ha_event",
            source_ref="camera.demo",
            payload={META_STREAM_URL: base_url, META_MOTION_VIDEO_TIME: "2026-02-27 08:16:30"},
        )
        job = await source.create_job(req)
        artifact = await source.fetch(job)

        playlist_path = Path(artifact.uri_or_path)
        assert playlist_path.exists()
        text = playlist_path.read_text(encoding="utf-8")
        assert 'URI="key_00000.key"' in text
        assert "segment_00000.ts" in text
        assert "segment_00001.ts" in text
        assert (playlist_path.parent / "key_00000.key").read_bytes() == b"secret-key"
        assert (playlist_path.parent / "segment_00000.ts").read_bytes() == b"segment-one"
        assert (playlist_path.parent / "segment_00001.ts").read_bytes() == b"segment-two"

    asyncio.run(scenario())


def test_http_m3u8_source_job_ids_are_unique_across_instances(tmp_path: Path) -> None:
    # Intent: prevent cross-instance temp-dir collisions from predictable job ids.
    # Expect: independent source instances still produce distinct job ids.
    async def scenario() -> None:
        source_a = HttpM3U8Source(temp_root=tmp_path / "a", fetch_bytes=lambda _: b"#EXTM3U\n")
        source_b = HttpM3U8Source(temp_root=tmp_path / "b", fetch_bytes=lambda _: b"#EXTM3U\n")
        req = SourceRequest(source_type="manual", source_ref="camera.demo")

        job_a = await source_a.create_job(req)
        job_b = await source_b.create_job(req)

        assert job_a.job_id != job_b.job_id

    asyncio.run(scenario())


def test_ffmpeg_processor_fallback_to_transcode(tmp_path: Path) -> None:
    # Intent: processing remains successful when fast remux path fails.
    # Expect: fallback transcode runs and returns a valid output artifact.
    async def scenario() -> None:
        calls: list[list[str]] = []

        async def fake_runner(cmd: list[str]) -> ProcessResult:
            calls.append(cmd)
            if len(calls) == 1:
                return ProcessResult(return_code=1, stderr="remux failed")
            output_path = Path(cmd[-1])
            output_path.write_bytes(b"ok")
            return ProcessResult(return_code=0, stderr="")

        source = HttpM3U8Source(temp_root=tmp_path, fetch_bytes=lambda _: b"#EXTM3U\n")
        req = SourceRequest(
            source_type="manual",
            source_ref="camera.demo",
            payload={META_STREAM_URL: "https://example.test/x.m3u8"},
        )
        job = await source.create_job(req)
        input_path = tmp_path / "input.m3u8"
        input_path.write_text("#EXTM3U\n", encoding="utf-8")

        processor = FfmpegProcessor(ffmpeg_path="ffmpeg", runner=fake_runner)
        artifact = await processor.process(job, Artifact(kind="input", uri_or_path=str(input_path)))

        assert len(calls) == 2
        assert artifact.meta["mode"] == "transcode_fallback"
        assert Path(artifact.uri_or_path).exists()

    asyncio.run(scenario())


def test_local_storage_store_and_policy(tmp_path: Path) -> None:
    # Intent: archive stores by event time and applies retention policy gates.
    # Expect: file is moved to dated path; old files deleted only when enabled.
    async def scenario() -> None:
        storage = LocalFsStorage(tmp_path / "storage")
        job_source = HttpM3U8Source(temp_root=tmp_path / "temp", fetch_bytes=lambda _: b"#EXTM3U\n")
        req = SourceRequest(
            source_type="ha_event",
            source_ref="camera.demo",
            payload={META_MOTION_VIDEO_TIME: "2026-02-27 08:16:30"},
        )
        job = await job_source.create_job(req)

        out_file = tmp_path / "temp" / "processed.mp4"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"video")

        record = await storage.store(job, Artifact(kind="output", uri_or_path=str(out_file)))
        stored_path = Path(record.location)
        assert stored_path.exists()
        assert not out_file.exists()
        assert stored_path.parent.name == "02"
        assert stored_path.parent.parent.name == "2026"
        assert re.fullmatch(r"camera_demo_2026_02_27_08_16_30(?:_\d{2})?\.mp4", stored_path.name)

        no_delete = await storage.apply_policy(RetentionPolicy())
        assert no_delete["deleted"] == 0

        old_file = tmp_path / "storage" / "2020" / "01" / "01" / "old.mp4"
        old_file.parent.mkdir(parents=True, exist_ok=True)
        old_file.write_bytes(b"old")
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
        os.utime(old_file, (old_ts, old_ts))

        result = await storage.apply_policy(
            RetentionPolicy(active_keep_count=0, retention_days=1, delete_enabled=True)
        )
        assert result["deleted"] >= 1
        assert not old_file.exists()

    asyncio.run(scenario())


def test_local_storage_uses_suffix_when_same_second_conflicts(tmp_path: Path) -> None:
    # Intent: avoid overwriting when multiple events share same second timestamp.
    # Expect: second archive file gets numeric suffix.
    async def scenario() -> None:
        storage = LocalFsStorage(tmp_path / "storage")
        source = HttpM3U8Source(temp_root=tmp_path / "temp", fetch_bytes=lambda _: b"#EXTM3U\n")
        req = SourceRequest(
            source_type="ha_event",
            source_ref="camera.demo",
            payload={META_MOTION_VIDEO_TIME: "2026-02-27 08:16:30"},
        )

        first_job = await source.create_job(req)
        first_out = tmp_path / "temp" / "a.mp4"
        first_out.parent.mkdir(parents=True, exist_ok=True)
        first_out.write_bytes(b"a")
        first_record = await storage.store(
            first_job, Artifact(kind="output", uri_or_path=str(first_out))
        )

        second_job = await source.create_job(req)
        second_out = tmp_path / "temp" / "b.mp4"
        second_out.write_bytes(b"b")
        second_record = await storage.store(
            second_job, Artifact(kind="output", uri_or_path=str(second_out))
        )

        first_path = Path(first_record.location)
        second_path = Path(second_record.location)
        assert first_path.name == "camera_demo_2026_02_27_08_16_30.mp4"
        assert second_path.name == "camera_demo_2026_02_27_08_16_30_01.mp4"
        assert first_path.exists()
        assert second_path.exists()

    asyncio.run(scenario())


def test_ffmpeg_processor_raises_when_both_attempts_fail(tmp_path: Path) -> None:
    # Intent: hard ffmpeg failures must surface as explicit processing errors.
    # Expect: RuntimeError is raised after remux and fallback both fail.
    async def scenario() -> None:
        async def fake_runner(_: list[str]) -> ProcessResult:
            return ProcessResult(return_code=1, stderr="failed")

        processor = FfmpegProcessor(ffmpeg_path="ffmpeg", runner=fake_runner)
        input_path = tmp_path / "input.m3u8"
        input_path.write_text("#EXTM3U\n", encoding="utf-8")

        with pytest.raises(RuntimeError):
            await processor.process(
                Job(job_id="job-1", source_ref="camera.demo"),
                Artifact(kind="input", uri_or_path=str(input_path)),
            )

    asyncio.run(scenario())


def test_ffmpeg_subprocess_cancel_releases_process(monkeypatch) -> None:
    # Intent: cancellation should not leak hanging subprocesses.
    # Expect: processor terminates process before propagating cancellation.
    async def scenario() -> None:
        class FakeProcess:
            def __init__(self) -> None:
                self.returncode = None
                self.terminated = False
                self.killed = False

            async def communicate(self):
                await asyncio.sleep(60)

            def terminate(self) -> None:
                self.terminated = True
                self.returncode = -15

            def kill(self) -> None:
                self.killed = True
                self.returncode = -9

            async def wait(self):
                return self.returncode

        fake = FakeProcess()

        async def fake_create_subprocess_exec(*args, **kwargs):
            return fake

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

        processor = FfmpegProcessor(ffmpeg_path="ffmpeg")
        task = asyncio.create_task(processor._run_subprocess(["ffmpeg", "-version"]))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert fake.terminated is True

    asyncio.run(scenario())


def test_ffmpeg_thumbnail_timeout_releases_process(monkeypatch) -> None:
    async def scenario() -> None:
        class FakeProcess:
            def __init__(self) -> None:
                self.returncode = None
                self.terminated = False

            async def communicate(self):
                await asyncio.sleep(60)

            def terminate(self) -> None:
                self.terminated = True
                self.returncode = -15

            def kill(self) -> None:
                self.returncode = -9

            async def wait(self):
                return self.returncode

        fake = FakeProcess()

        async def fake_create_subprocess_exec(*args, **kwargs):
            return fake

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
        processor = FfmpegProcessor(ffmpeg_path="ffmpeg", thumbnail_timeout=0.001)

        with pytest.raises(RuntimeError, match="thumbnail extraction timed out"):
            await processor._run_thumbnail_subprocess(["ffmpeg", "-version"])

        assert fake.terminated is True

    asyncio.run(scenario())
