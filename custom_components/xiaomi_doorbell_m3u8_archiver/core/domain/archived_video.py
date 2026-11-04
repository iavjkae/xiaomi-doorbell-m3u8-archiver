"""Metadata exposed by an archive catalog."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ArchivedVideo:
    """A stable archive identifier with capture time and local playback path."""

    identifier: str
    captured_at: datetime
    path: Path
