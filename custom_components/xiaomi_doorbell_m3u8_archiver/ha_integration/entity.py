"""Shared Home Assistant entity metadata."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from ..const import CONF_NAME_DEFAULT, DOMAIN


def archive_device_info(entry_id: str, camera_entity_id: str | None) -> DeviceInfo:
    """Describe the archive service device for one config entry."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        manufacturer="Xiaomi",
        model=CONF_NAME_DEFAULT,
        name=camera_entity_id or CONF_NAME_DEFAULT,
    )
