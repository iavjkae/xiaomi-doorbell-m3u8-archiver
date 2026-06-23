from unittest.mock import MagicMock

from custom_components.xiaomi_doorbell_m3u8_archiver.const import (
    DOMAIN,
    SENSOR_CURRENT_JOB_STATUS,
    SENSOR_IS_BUSY,
    SENSOR_TOTAL_DONE,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.binary_sensor import (
    RuntimeBusyBinarySensor,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.sensor import (
    RuntimeMetricSensor,
)


def test_metric_sensor_uses_translation_key() -> None:
    runtime = MagicMock()
    runtime.metrics.return_value = {SENSOR_TOTAL_DONE: 3}

    entity = RuntimeMetricSensor(runtime, "entry-1", SENSOR_TOTAL_DONE)

    assert entity.translation_key == SENSOR_TOTAL_DONE
    assert entity.has_entity_name is True
    assert entity.unique_id == f"{DOMAIN}_entry-1_{SENSOR_TOTAL_DONE}"
    assert entity.native_value == 3


def test_status_sensor_uses_translation_key() -> None:
    runtime = MagicMock()
    runtime.metrics.return_value = {SENSOR_CURRENT_JOB_STATUS: "processing"}

    entity = RuntimeMetricSensor(runtime, "entry-1", SENSOR_CURRENT_JOB_STATUS)

    assert entity.translation_key == SENSOR_CURRENT_JOB_STATUS
    assert entity.has_entity_name is True
    assert entity.native_value == "processing"


def test_busy_binary_sensor_uses_translation_key() -> None:
    runtime = MagicMock()
    runtime.metrics.return_value = {SENSOR_IS_BUSY: True}

    entity = RuntimeBusyBinarySensor(runtime, "entry-1")

    assert entity.translation_key == SENSOR_IS_BUSY
    assert entity.has_entity_name is True
    assert entity.unique_id == f"{DOMAIN}_entry-1_{SENSOR_IS_BUSY}"
    assert entity.is_on is True
