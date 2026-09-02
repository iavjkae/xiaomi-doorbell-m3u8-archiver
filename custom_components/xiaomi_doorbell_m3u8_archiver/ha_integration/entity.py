"""Shared Home Assistant entity metadata."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry, DeviceEntryType, DeviceInfo

from ..const import CONF_NAME_DEFAULT, DOMAIN


def archive_device_info(entry_id: str, camera_entity_id: str | None) -> DeviceInfo:
    """Describe the archive service device for one config entry."""
    return DeviceInfo(
        entry_type=DeviceEntryType.SERVICE,
        identifiers={(DOMAIN, entry_id)},
        manufacturer="Xiaomi",
        model=CONF_NAME_DEFAULT,
        name=camera_entity_id or CONF_NAME_DEFAULT,
    )


def async_register_archive_device(
    hass: HomeAssistant,
    entry_id: str,
    device_name: str | None,
) -> DeviceEntry:
    """Explicitly register the virtual archive device for a config entry."""
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry_id,
        entry_type=DeviceEntryType.SERVICE,
        identifiers={(DOMAIN, entry_id)},
        manufacturer="Xiaomi",
        model=CONF_NAME_DEFAULT,
        name=device_name or CONF_NAME_DEFAULT,
    )
