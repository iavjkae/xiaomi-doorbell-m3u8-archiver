"""Binary sensor platform for Xiaomi Doorbell M3U8 Archiver."""

from .ha_integration.binary_sensor import SCAN_INTERVAL, async_setup_entry

__all__ = ["SCAN_INTERVAL", "async_setup_entry"]
