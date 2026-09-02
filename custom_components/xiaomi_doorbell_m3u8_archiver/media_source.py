"""Home Assistant media source platform entrypoint."""

from .ha_integration.media_source import async_get_media_source

__all__ = ["async_get_media_source"]
