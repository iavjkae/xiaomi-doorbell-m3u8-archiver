"""FFmpeg processor adapter."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ...core.domain import Artifact, Job
from ...core.lifecycle import FileCleanupHandle, JobContext, ProcessCleanupHandle
from ..common.process_result import ProcessResult


class FfmpegProcessor:
    """FFmpeg-backed video processor with segmented merge strategy."""

    THUMBNAIL_TIMEOUT_SECONDS = 15

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        runner=None,
        thumbnail_runner=None,
        thumbnail_timeout: float = THUMBNAIL_TIMEOUT_SECONDS,
    ) -> None:
        self._ffmpeg_path = ffmpeg_path
        self._runner = runner or self._run_subprocess
        self._thumbnail_runner = thumbnail_runner or self._run_thumbnail_subprocess
        self._thumbnail_timeout = thumbnail_timeout

    async def extract_thumbnail(self, video_path: Path) -> bytes:
        """Extract a representative JPEG frame without modifying the video."""
        command = [
            self._ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "0.5",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            "scale=640:-2",
            "-an",
            "-f",
            "image2pipe",
            "-c:v",
            "mjpeg",
            "pipe:1",
        ]
        return await self._thumbnail_runner(command)

    async def process(
        self, job: Job, input_artifact: Artifact, *, context: JobContext | None = None
    ) -> Artifact:
        input_path = Path(input_artifact.uri_or_path)
        output_path = input_path.parent / "processed.mp4"
        part_playlists = await self._build_part_playlists(input_path, context)
        if len(part_playlists) <= 1:
            return await self._process_single_playlist(input_path, output_path)

        part_outputs: list[Path] = []
        for index, part_playlist in enumerate(part_playlists):
            part_output = input_path.parent / f"part_{index:05d}.mp4"
            await self._process_single_playlist(part_playlist, part_output)
            part_outputs.append(part_output)
            if context is not None:
                context.register_cleanup(FileCleanupHandle(path=part_output))

        concat_list_path = input_path.parent / "concat_list.txt"
        concat_lines = [f"file '{path}'" for path in part_outputs]
        await asyncio.to_thread(
            concat_list_path.write_text,
            "\n".join(concat_lines) + "\n",
            encoding="utf-8",
        )
        if context is not None:
            context.register_cleanup(FileCleanupHandle(path=concat_list_path))

        concat_cmd = [
            self._ffmpeg_path,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list_path),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        concat_result = await self._runner(concat_cmd)
        if concat_result.return_code == 0:
            return Artifact(
                kind="output", uri_or_path=str(output_path), meta={"mode": "concat_copy"}
            )

        # Copy-first preserves quality and speed, while transcode fallback keeps the
        # pipeline resilient to mixed codecs/containers in camera streams.
        concat_fallback_cmd = [
            self._ffmpeg_path,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list_path),
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        concat_fallback_result = await self._runner(concat_fallback_cmd)
        if concat_fallback_result.return_code != 0:
            raise RuntimeError(
                "ffmpeg concat failed; "
                f"copy={concat_result.stderr[-500:]}; fallback={concat_fallback_result.stderr[-500:]}"
            )

        return Artifact(
            kind="output", uri_or_path=str(output_path), meta={"mode": "concat_transcode_fallback"}
        )

    async def _process_single_playlist(self, input_path: Path, output_path: Path) -> Artifact:
        remux_cmd = [
            self._ffmpeg_path,
            "-y",
            "-protocol_whitelist",
            "file,crypto,data,https,tls,tcp",
            "-allowed_extensions",
            "ALL",
            "-fflags",
            "+genpts",
            "-i",
            str(input_path),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        remux_result = await self._runner(remux_cmd)
        if remux_result.return_code == 0:
            return Artifact(kind="output", uri_or_path=str(output_path), meta={"mode": "remux"})

        # Some streams cannot be remuxed cleanly; controlled transcode trades CPU for
        # a stable output artifact instead of failing the whole job.
        fallback_cmd = [
            self._ffmpeg_path,
            "-y",
            "-protocol_whitelist",
            "file,crypto,data,https,tls,tcp",
            "-allowed_extensions",
            "ALL",
            "-fflags",
            "+genpts",
            "-i",
            str(input_path),
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        fallback_result = await self._runner(fallback_cmd)
        if fallback_result.return_code != 0:
            raise RuntimeError(
                "ffmpeg processing failed; "
                f"remux={remux_result.stderr[-500:]}; fallback={fallback_result.stderr[-500:]}"
            )

        return Artifact(
            kind="output", uri_or_path=str(output_path), meta={"mode": "transcode_fallback"}
        )

    async def _build_part_playlists(
        self, input_path: Path, context: JobContext | None = None
    ) -> list[Path]:
        content = await asyncio.to_thread(input_path.read_text, encoding="utf-8")
        lines = content.splitlines()
        extinf_positions = [
            idx for idx, line in enumerate(lines) if line.strip().startswith("#EXTINF")
        ]
        if len(extinf_positions) <= 1:
            return [input_path]

        header_end = extinf_positions[0]
        header = lines[:header_end]
        tail = len(lines)
        part_paths: list[Path] = []

        for idx, start in enumerate(extinf_positions):
            end = extinf_positions[idx + 1] if idx + 1 < len(extinf_positions) else tail
            part_lines = header + lines[start:end]
            if not part_lines or part_lines[-1].strip() != "#EXT-X-ENDLIST":
                part_lines.append("#EXT-X-ENDLIST")

            part_path = input_path.parent / f"part_{idx:05d}.m3u8"
            await asyncio.to_thread(
                part_path.write_text,
                "\n".join(part_lines) + "\n",
                encoding="utf-8",
            )
            part_paths.append(part_path)
            if context is not None:
                context.register_cleanup(FileCleanupHandle(path=part_path))

        return part_paths

    async def _run_subprocess(self, command: list[str]) -> ProcessResult:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        process_handle = ProcessCleanupHandle(process=process)
        try:
            _stdout, stderr = await process.communicate()
            return ProcessResult(return_code=process.returncode or 0, stderr=stderr.decode())
        except asyncio.CancelledError:
            await process_handle.cleanup()
            raise

    async def _run_thumbnail_subprocess(self, command: list[str]) -> bytes:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        process_handle = ProcessCleanupHandle(process=process)
        try:
            async with asyncio.timeout(self._thumbnail_timeout):
                stdout, stderr = await process.communicate()
            if process.returncode != 0 or not stdout:
                detail = stderr.decode(errors="replace")[-500:]
                raise RuntimeError(f"ffmpeg thumbnail extraction failed: {detail}")
            return stdout
        except TimeoutError as exc:
            await process_handle.cleanup()
            raise RuntimeError("ffmpeg thumbnail extraction timed out") from exc
        except asyncio.CancelledError:
            await process_handle.cleanup()
            raise
