"""Binary sensor entities exposing runtime flags."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from ..const import CONF_CAMERA_ENTITY_ID, DOMAIN, SENSOR_IS_BUSY
from .coordinator import IntegrationRuntime
from .entity import archive_device_info

SCAN_INTERVAL = timedelta(seconds=5)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up runtime binary sensors via config entry."""
    domain_data = hass.data.get(DOMAIN, {})
    runtimes = domain_data.get("runtimes", {})
    runtime: IntegrationRuntime | None = runtimes.get(entry.entry_id)
    if runtime is None:
        return

    async_add_entities(
        [
            RuntimeBusyBinarySensor(
                runtime,
                entry.entry_id,
                entry.data.get(CONF_CAMERA_ENTITY_ID),
            )
        ],
        True,
    )


class RuntimeBusyBinarySensor(BinarySensorEntity):
    """Expose runtime busy/idle state as a binary sensor."""

    _attr_should_poll = True
    _attr_has_entity_name = True

    def __init__(
        self,
        runtime: IntegrationRuntime,
        entry_id: str,
        camera_entity_id: str | None = None,
    ) -> None:
        self._runtime = runtime
        self._attr_translation_key = SENSOR_IS_BUSY
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_{SENSOR_IS_BUSY}"
        self._attr_device_info = archive_device_info(entry_id, camera_entity_id)

    @property
    def is_on(self) -> bool:
        return bool(self._runtime.metrics().get(SENSOR_IS_BUSY, False))
