from unittest.mock import MagicMock, patch

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers.device_registry import DeviceEntryType

from custom_components.xiaomi_doorbell_m3u8_archiver.const import (
    DOMAIN,
    SENSOR_CURRENT_JOB_STATUS,
    SENSOR_IS_BUSY,
    SENSOR_LAST_JOB_STATUS,
    SENSOR_TOTAL_DONE,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.core.domain import JobStatus
from custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.binary_sensor import (
    RuntimeBusyBinarySensor,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.entity import (
    async_register_archive_device,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.sensor import (
    RuntimeMetricSensor,
)


def test_metric_sensor_uses_translation_key() -> None:
    runtime = MagicMock()
    runtime.metrics.return_value = {SENSOR_TOTAL_DONE: 3}

    entity = RuntimeMetricSensor(runtime, "entry-1", SENSOR_TOTAL_DONE, "camera.front")

    assert entity.translation_key == SENSOR_TOTAL_DONE
    assert entity.has_entity_name is True
    assert entity.unique_id == f"{DOMAIN}_entry-1_{SENSOR_TOTAL_DONE}"
    assert entity.native_value == 3
    assert entity.device_class is None
    assert entity.options is None
    assert entity.device_info["identifiers"] == {(DOMAIN, "entry-1")}
    assert entity.device_info["entry_type"] == DeviceEntryType.SERVICE


def test_status_sensors_use_enum_state_translations() -> None:
    runtime = MagicMock()
    runtime.metrics.return_value = {
        SENSOR_CURRENT_JOB_STATUS: "processing",
        SENSOR_LAST_JOB_STATUS: "done",
    }

    for metric_key, expected_value in (
        (SENSOR_CURRENT_JOB_STATUS, "processing"),
        (SENSOR_LAST_JOB_STATUS, "done"),
    ):
        entity = RuntimeMetricSensor(runtime, "entry-1", metric_key)

        assert entity.translation_key == metric_key
        assert entity.has_entity_name is True
        assert entity.native_value == expected_value
        assert entity.device_class is SensorDeviceClass.ENUM
        assert entity.options == [status.value for status in JobStatus]


def test_busy_binary_sensor_uses_translation_key() -> None:
    runtime = MagicMock()
    runtime.metrics.return_value = {SENSOR_IS_BUSY: True}

    entity = RuntimeBusyBinarySensor(runtime, "entry-1", "camera.front")

    assert entity.translation_key == SENSOR_IS_BUSY
    assert entity.has_entity_name is True
    assert entity.unique_id == f"{DOMAIN}_entry-1_{SENSOR_IS_BUSY}"
    assert entity.is_on is True
    assert entity.device_info["identifiers"] == {(DOMAIN, "entry-1")}
    assert entity.device_info["entry_type"] == DeviceEntryType.SERVICE


def test_archive_device_is_explicitly_registered() -> None:
    hass = MagicMock()
    registry = MagicMock()

    with patch(
        "custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.entity.dr.async_get",
        return_value=registry,
    ):
        async_register_archive_device(hass, "entry-1", "Front door archive")

    registry.async_get_or_create.assert_called_once_with(
        config_entry_id="entry-1",
        entry_type=DeviceEntryType.SERVICE,
        identifiers={(DOMAIN, "entry-1")},
        manufacturer="Xiaomi",
        model="Xiaomi Doorbell M3U8 Archiver",
        name="Front door archive",
    )
