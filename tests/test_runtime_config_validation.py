import asyncio

from custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.coordinator import (
    IntegrationRuntime,
)


def test_async_from_config_reports_missing_keys() -> None:
    # Intent: setup failures should explain which config keys are missing.
    # Expect: ValueError includes a readable list of missing keys.
    async def scenario() -> None:
        try:
            await IntegrationRuntime.async_from_config({}, "entry-1", {})
            assert False, "expected ValueError"
        except ValueError as exc:
            message = str(exc)
            assert "Missing config keys" in message
            assert "fetch_concurrency" in message
            assert "storage_root" in message

    asyncio.run(scenario())
