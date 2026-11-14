"""Resolve Home Assistant entity state into source requests."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from ..const import META_MOTION_VIDEO_TIME, META_STREAM_URL
from ..core.domain import SourceRequest


def build_source_request(
    entity_id: str,
    attributes: Mapping[str, Any],
    source_type: str,
    event_time: str | None = None,
) -> SourceRequest | None:
    """Build a SourceRequest from entity attributes.

    Returns None when mandatory stream metadata is unavailable.
    """
    stream_url = _as_nonempty_str(attributes.get("stream_address"))
    if stream_url is None:
        return None

    resolved_event_time = _resolve_event_time(event_time, attributes)
    payload = {
        META_STREAM_URL: stream_url,
        META_MOTION_VIDEO_TIME: resolved_event_time,
    }
    return SourceRequest(
        source_type=source_type,
        source_ref=entity_id,
        payload=payload,
    )


def should_enqueue_motion_event(
    old_attributes: Mapping[str, Any] | None,
    new_attributes: Mapping[str, Any] | None,
) -> bool:
    """Return True only when motion_video_time changed to a non-empty value."""
    if not new_attributes:
        return False

    new_motion = _as_nonempty_str(new_attributes.get("motion_video_time"))
    if new_motion is None:
        return False

    old_motion = _as_nonempty_str((old_attributes or {}).get("motion_video_time"))
    return old_motion != new_motion


def _resolve_event_time(event_time: str | None, attributes: Mapping[str, Any]) -> str:
    if event_time:
        return event_time

    motion_time = _as_nonempty_str(attributes.get("motion_video_time"))
    if motion_time is not None:
        return motion_time

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _as_nonempty_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text
