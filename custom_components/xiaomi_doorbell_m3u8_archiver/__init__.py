"""Home Assistant integration bootstrap."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .const import CONF_CAMERA_ENTITY_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor", "binary_sensor"]

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up integration base (UI config only)."""
    from .ha_integration.frontend import async_register_frontend

    hass.data.setdefault(
        DOMAIN,
        {"runtimes": {}, "entry_camera_entity": {}, "services_registered": False},
    )
    await async_register_frontend(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up integration from UI config entry."""
    platforms_loaded = False
    try:
        from .ha_integration.coordinator import IntegrationRuntime
        from .ha_integration.entity import async_register_archive_device
        from .ha_integration.listener import async_setup_listener
        from .ha_integration.services import async_register_services, async_unregister_services

        domain_data = hass.data.setdefault(
            DOMAIN,
            {"runtimes": {}, "entry_camera_entity": {}, "services_registered": False},
        )
        runtimes: dict[str, IntegrationRuntime] = domain_data["runtimes"]
        entry_camera_entity: dict[str, str] = domain_data["entry_camera_entity"]

        runtime: IntegrationRuntime | None = None
        camera_entity_id = entry.data.get(CONF_CAMERA_ENTITY_ID)

        _LOGGER.info("Starting setup for %s entry: %s", DOMAIN, entry.entry_id)
        thumbnail_slots = domain_data.setdefault("thumbnail_slots", asyncio.Semaphore(2))
        legacy_sources = domain_data.setdefault("legacy_sources", set())
        legacy_sources.update(
            configured.data[CONF_CAMERA_ENTITY_ID]
            for configured in hass.config_entries.async_entries(DOMAIN)
            if configured.data.get(CONF_CAMERA_ENTITY_ID)
        )
        if camera_entity_id:
            legacy_sources.add(camera_entity_id)
        runtime = await IntegrationRuntime.async_from_config(
            hass,
            entry.entry_id,
            entry.data,
            thumbnail_slots=thumbnail_slots,
            legacy_sources=legacy_sources,
        )
        runtimes[entry.entry_id] = runtime
        if camera_entity_id:
            entry_camera_entity[entry.entry_id] = camera_entity_id

        await runtime.start()

        if camera_entity_id:
            runtime.unsub_listener = async_setup_listener(hass, runtime, camera_entity_id)
            _LOGGER.info("Listening Xiaomi doorbell entity: %s", camera_entity_id)

        async_register_archive_device(
            hass,
            entry.entry_id,
            entry.title or camera_entity_id,
        )
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        platforms_loaded = True
        await async_register_services(hass)
        _LOGGER.info("%s entry setup complete: %s", DOMAIN, entry.entry_id)
        return True
    except Exception:
        _LOGGER.exception("Failed to setup %s entry: %s", DOMAIN, entry.entry_id)
        domain_data = hass.data.get(DOMAIN, {})
        runtimes = domain_data.get("runtimes", {})
        entry_camera_entity = domain_data.get("entry_camera_entity", {})
        runtime = runtimes.get(entry.entry_id)

        # Entry setup is treated as transactional: any partial setup must be rolled
        # back to avoid leaked workers/listeners on failed reloads.
        if platforms_loaded:
            await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        if runtime is not None:
            await runtime.stop()
        runtimes.pop(entry.entry_id, None)
        entry_camera_entity.pop(entry.entry_id, None)
        if not runtimes:
            try:
                from .ha_integration.services import async_unregister_services

                await async_unregister_services(hass)
            except Exception:
                _LOGGER.exception("Failed to unregister services after setup rollback")
        raise


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload integration config entry."""
    from .ha_integration.services import async_unregister_services

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        # Keep runtime in memory when platform unload fails so HA state remains
        # internally consistent and operators can retry unload safely.
        return False

    domain_data = hass.data.get(DOMAIN, {})
    runtimes: dict[str, Any] = domain_data.get("runtimes", {})
    entry_camera_entity: dict[str, str] = domain_data.get("entry_camera_entity", {})
    runtime = runtimes.pop(entry.entry_id, None)
    entry_camera_entity.pop(entry.entry_id, None)
    if runtime is not None:
        await runtime.stop()

    if not runtimes:
        await async_unregister_services(hass)

    return unload_ok
