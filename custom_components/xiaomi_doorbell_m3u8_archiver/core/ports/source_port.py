"""Source adapter interface."""

from __future__ import annotations

from typing import Protocol

from ..domain import Artifact, Job, SourceRequest
from ..lifecycle import JobContext


class SourcePort(Protocol):
    """Source adapter interface."""

    async def create_job(self, request: SourceRequest) -> Job:
        """Create a Job from an incoming source request."""

    async def fetch(self, job: Job, *, context: JobContext | None = None) -> Artifact:
        """Fetch source media and produce input artifact."""
