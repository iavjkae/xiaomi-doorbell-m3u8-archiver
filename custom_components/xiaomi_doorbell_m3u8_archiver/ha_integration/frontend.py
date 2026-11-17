"""Register the archive dashboard card with Home Assistant frontend."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from ..const import DOMAIN

CARD_VERSION = "0.4.2"
CARD_URL_PATH = f"/{DOMAIN}/archive-card.js"
CARD_MODULE_URL = f"{CARD_URL_PATH}?v={CARD_VERSION}"
CARD_FILE = Path(__file__).parents[1] / "frontend" / "archive-card.js"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Serve and load the integration's dashboard card."""
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_PATH, str(CARD_FILE), True)]
    )
    frontend.add_extra_js_url(hass, CARD_MODULE_URL)
