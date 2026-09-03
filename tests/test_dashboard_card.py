import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.frontend import (
    CARD_FILE,
    CARD_MODULE_URL,
    CARD_URL_PATH,
    async_register_frontend,
)


def test_dashboard_card_is_registered_as_frontend_module() -> None:
    async def scenario() -> None:
        hass = MagicMock()
        hass.http.async_register_static_paths = AsyncMock()

        with patch(
            "custom_components.xiaomi_doorbell_m3u8_archiver.ha_integration.frontend.frontend.add_extra_js_url"
        ) as add_extra_js_url:
            await async_register_frontend(hass)

        configs = hass.http.async_register_static_paths.await_args.args[0]
        assert len(configs) == 1
        assert configs[0].url_path == CARD_URL_PATH
        assert configs[0].path == str(CARD_FILE)
        assert configs[0].cache_headers is True
        add_extra_js_url.assert_called_once_with(hass, CARD_MODULE_URL)

    asyncio.run(scenario())


def test_dashboard_card_version_matches_integration_version() -> None:
    manifest_path = CARD_FILE.parents[1] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert CARD_MODULE_URL.endswith(f"?v={manifest['version']}")


def test_dashboard_card_uses_media_source_and_lazy_resolution() -> None:
    source = Path(CARD_FILE).read_text(encoding="utf-8")

    assert "xiaomi-doorbell-archive-card" in source
    assert "media_source/browse_media" in source
    assert "media_source/resolve_media" in source
    assert "item.thumbnail" in source
    assert "video.poster = item.thumbnail" in source
    assert 'title: "今日门铃视频"' in source
    assert "new Map()" not in source
    assert "setInterval" not in source
    assert "autoplay" not in source
