import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.xiaomi_doorbell_m3u8_archiver.const import (
    DOMAIN,
    SERVICE_ENQUEUE,
    SERVICE_GET_STATUS,
)
from custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.services import (
    async_register_services,
)


class FakeServices:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def async_register(
        self,
        domain,
        service,
        handler,
        schema=None,
        supports_response=None,
    ) -> None:
        self.handlers[service] = handler

    def has_service(self, domain, service) -> bool:
        return service in self.handlers

    def async_remove(self, domain, service) -> None:
        self.handlers.pop(service, None)


class FakeStates:
    def __init__(self, states: dict[str, object] | None = None) -> None:
        self._states = states or {}

    def get(self, entity_id: str):
        return self._states.get(entity_id)


class FakeHass:
    def __init__(self, domain_data: dict, states: dict[str, object] | None = None) -> None:
        self.data = {DOMAIN: domain_data}
        self.services = FakeServices()
        self.states = FakeStates(states)


def test_get_status_counts_busy_instances_from_metric_flag() -> None:
    async def scenario() -> None:
        runtime_busy = MagicMock()
        runtime_busy.metrics.return_value = {
            "total_done": 3,
            "total_failed": 1,
            "processed_today": 2,
            "current_job_status": "fetching",
            "is_busy": True,
        }
        runtime_idle = MagicMock()
        runtime_idle.metrics.return_value = {
            "total_done": 4,
            "total_failed": 0,
            "processed_today": 1,
            "current_job_status": "done",
            "is_busy": False,
        }
        hass = FakeHass(
            {
                "runtimes": {"entry-1": runtime_busy, "entry-2": runtime_idle},
                "entry_camera_entity": {
                    "entry-1": "camera.a",
                    "entry-2": "camera.b",
                },
                "services_registered": False,
            }
        )

        await async_register_services(hass)
        result = await hass.services.handlers[SERVICE_GET_STATUS](SimpleNamespace(data={}))

        assert result["summary"]["busy_instances"] == 1

    asyncio.run(scenario())


@pytest.mark.asyncio
async def test_enqueue_raises_translated_error_when_entity_missing() -> None:
    runtime = MagicMock()
    runtime.enqueue_request = AsyncMock()
    hass = FakeHass(
        {
            "runtimes": {"entry-1": runtime},
            "entry_camera_entity": {"entry-1": "camera.a"},
            "services_registered": False,
        }
    )

    await async_register_services(hass)

    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.handlers[SERVICE_ENQUEUE](
            SimpleNamespace(data={"entity_id": "camera.a"})
        )

    assert exc_info.value.translation_domain == DOMAIN
    assert exc_info.value.translation_key == "entity_not_found"


@pytest.mark.asyncio
async def test_get_status_raises_translated_error_for_unknown_entry() -> None:
    hass = FakeHass(
        {
            "runtimes": {"entry-1": MagicMock()},
            "entry_camera_entity": {"entry-1": "camera.a"},
            "services_registered": False,
        }
    )

    await async_register_services(hass)

    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.handlers[SERVICE_GET_STATUS](
            SimpleNamespace(data={"entry_id": "missing"})
        )

    assert exc_info.value.translation_domain == DOMAIN
    assert exc_info.value.translation_key == "unknown_entry_id"
