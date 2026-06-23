"""Sensor entities exposing runtime metrics."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from ..const import (
    DOMAIN,
    SENSOR_CURRENT_JOB_STATUS,
    SENSOR_LAST_JOB_STATUS,
    SENSOR_PROCESSED_TODAY,
    SENSOR_QUEUE_FETCH,
    SENSOR_QUEUE_PROCESS,
    SENSOR_TOTAL_DONE,
    SENSOR_TOTAL_FAILED,
)
from .coordinator import IntegrationRuntime

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
        RuntimeMetricSensor(runtime, entry.entry_id, metric_key)
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
    ) -> None:
        self._runtime = runtime
        self._metric_key = metric_key
        self._attr_translation_key = metric_key
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_{metric_key}"
        self._attr_native_value: Any = None

    @property
    def native_value(self) -> Any:
        return self._runtime.metrics().get(self._metric_key)
