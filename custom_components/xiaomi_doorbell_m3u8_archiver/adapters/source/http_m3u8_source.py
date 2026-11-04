"""HTTP m3u8 source adapter."""

from __future__ import annotations

import asyncio
import re
import tempfile
from uuid import uuid4
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen

from ...const import META_STREAM_URL
from ...core.domain import Artifact, Job, SourceRequest
from ...core.lifecycle import DirectoryCleanupHandle, JobContext


def _fetch_bytes_default(url: str) -> bytes:
    with urlopen(url, timeout=30) as response:  # noqa: S310
        return response.read()


class HttpM3U8Source:
    """Download remote m3u8 and referenced media segments into local temp directory."""

    def __init__(self, temp_root: Path | None = None, fetch_bytes=None) -> None:
        self._temp_root = temp_root or Path(tempfile.gettempdir()) / "xdma_jobs"
        self._fetch_bytes = fetch_bytes or _fetch_bytes_default

    async def create_job(self, request: SourceRequest) -> Job:
        # UUID-based ids avoid collisions across HA restarts and multiple runtime
        # instances sharing the same temp root.
        job_id = f"job-{uuid4().hex}"
        metadata = request.payload.copy()
        metadata["source_type"] = request.source_type
        return Job(job_id=job_id, source_ref=request.source_ref, metadata=metadata)

    async def fetch(self, job: Job, *, context: JobContext | None = None) -> Artifact:
        stream_url = str(job.metadata.get(META_STREAM_URL, "")).strip()
        if not stream_url:
            raise ValueError("Missing stream_url in job metadata")

        job_temp = self._temp_root / job.job_id
        await asyncio.to_thread(job_temp.mkdir, parents=True, exist_ok=True)
        if context is not None:
            context.register_cleanup(DirectoryCleanupHandle(path=job_temp))

        m3u8_content = await asyncio.to_thread(self._fetch_bytes, stream_url)
        playlist_lines = m3u8_content.decode("utf-8", errors="replace").splitlines()
        rewritten = []
        segment_index = 0
        key_index = 0

        for line in playlist_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                if stripped.startswith("#EXT-X-KEY"):
                    rewritten_key_line = await self._rewrite_key_line(
                        line=line,
                        stream_url=stream_url,
                        job_temp=job_temp,
                        key_index=key_index,
                    )
                    rewritten.append(rewritten_key_line)
                    key_index += 1
                    continue
                rewritten.append(line)
                continue

            segment_url = urljoin(stream_url, stripped)
            ext = Path(urlparse(segment_url).path).suffix or ".ts"
            local_name = f"segment_{segment_index:05d}{ext}"
            segment_path = job_temp / local_name
            segment_bytes = await asyncio.to_thread(self._fetch_bytes, segment_url)
            await asyncio.to_thread(segment_path.write_bytes, segment_bytes)

            rewritten.append(local_name)
            segment_index += 1

        local_playlist = job_temp / "input.m3u8"
        # Rewriting to local segment/key paths makes ffmpeg deterministic and avoids
        # relying on remote network access during processing.
        playlist_text = "\n".join(rewritten) + "\n"
        await asyncio.to_thread(local_playlist.write_text, playlist_text, encoding="utf-8")

        return Artifact(
            kind="input",
            uri_or_path=str(local_playlist),
            meta={
                "job_temp_dir": str(job_temp),
                "segment_count": segment_index,
                "key_count": key_index,
            },
        )

    async def _rewrite_key_line(
        self,
        line: str,
        stream_url: str,
        job_temp: Path,
        key_index: int,
    ) -> str:
        match = re.search(r'URI="([^"]+)"', line)
        if not match:
            return line

        key_uri = match.group(1)
        key_url = urljoin(stream_url, key_uri)
        ext = Path(urlparse(key_url).path).suffix or ".key"
        local_name = f"key_{key_index:05d}{ext}"
        key_path = job_temp / local_name
        key_bytes = await asyncio.to_thread(self._fetch_bytes, key_url)
        await asyncio.to_thread(key_path.write_bytes, key_bytes)
        return line.replace(f'URI="{key_uri}"', f'URI="{local_name}"', 1)
