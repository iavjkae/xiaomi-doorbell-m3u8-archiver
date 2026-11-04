"""Sensor entities exposing runtime metrics."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from ..const import (
    CONF_CAMERA_ENTITY_ID,
    DOMAIN,
    SENSOR_CURRENT_JOB_STATUS,
    SENSOR_LAST_JOB_STATUS,
    SENSOR_PROCESSED_TODAY,
    SENSOR_QUEUE_FETCH,
    SENSOR_QUEUE_PROCESS,
    SENSOR_TOTAL_DONE,
    SENSOR_TOTAL_FAILED,
)
from ..core.domain import JobStatus
from .coordinator import IntegrationRuntime
from .entity import archive_device_info

SCAN_INTERVAL = timedelta(seconds=5)

SENSOR_DEFINITIONS: tuple[str, ...] = (
    SENSOR_TOTAL_DONE,
    SENSOR_TOTAL_FAILED,
    SENSOR_PROCESSED_TODAY,
    SENSOR_QUEUE_FETCH,
    SENSOR_QUEUE_PROCESS,
    SENSOR_LAST_JOB_STATUS,
    SENSOR_CURRENT_JOB_STATUS,
)

STATUS_SENSOR_KEYS = {SENSOR_LAST_JOB_STATUS, SENSOR_CURRENT_JOB_STATUS}
JOB_STATUS_OPTIONS = [status.value for status in JobStatus]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up runtime metric sensors via config entry."""
    domain_data = hass.data.get(DOMAIN, {})
    runtimes = domain_data.get("runtimes", {})
    runtime: IntegrationRuntime | None = runtimes.get(entry.entry_id)
    if runtime is None:
        return

    entities = [
        RuntimeMetricSensor(
            runtime,
            entry.entry_id,
            metric_key,
            entry.data.get(CONF_CAMERA_ENTITY_ID),
        )
        for metric_key in SENSOR_DEFINITIONS
    ]
    async_add_entities(entities, True)


class RuntimeMetricSensor(SensorEntity):
    """Expose one runtime metric as an individual sensor entity."""

    _attr_should_poll = True
    _attr_has_entity_name = True

    def __init__(
        self,
        runtime: IntegrationRuntime,
        entry_id: str,
        metric_key: str,
        camera_entity_id: str | None = None,
    ) -> None:
        self._runtime = runtime
        self._metric_key = metric_key
        self._attr_translation_key = metric_key
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_{metric_key}"
        self._attr_device_info = archive_device_info(entry_id, camera_entity_id)
        if metric_key in STATUS_SENSOR_KEYS:
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = JOB_STATUS_OPTIONS

    @property
    def native_value(self) -> Any:
        return self._runtime.metrics().get(self._metric_key)
