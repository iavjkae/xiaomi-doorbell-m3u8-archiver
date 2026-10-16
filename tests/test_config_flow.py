import pytest
from unittest.mock import patch, MagicMock

from custom_components.xiaomi_doorbell_m3u8_archiver.config_flow import XiaomiDoorbellM3U8ArchiverConfigFlow
from custom_components.xiaomi_doorbell_m3u8_archiver.const import (
    CONF_CAMERA_ENTITY_ID,
    CONF_STORAGE_ROOT,
    CONF_FFMPEG_PATH,
)

@pytest.fixture
def flow():
    flow = XiaomiDoorbellM3U8ArchiverConfigFlow()
    flow.hass = MagicMock()
    return flow

@pytest.mark.asyncio
async def test_flow_user_step_init(flow):
    """Test the initial user step shows the form."""
    result = await flow.async_step_user()
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert not result["errors"]

@pytest.mark.asyncio
async def test_flow_user_step_happy_path(flow):
    """Test the happy path where all inputs are valid."""
    user_input = {
        CONF_CAMERA_ENTITY_ID: "camera.doorbell",
        CONF_STORAGE_ROOT: "/tmp/video",
        CONF_FFMPEG_PATH: "/usr/bin/ffmpeg",
    }
    
    with (
        patch("os.path.exists", return_value=True),
        patch("os.access", return_value=True),
        patch("shutil.which", return_value="/usr/bin/ffmpeg"),
        patch.object(flow, "async_set_unique_id"),
        patch.object(flow, "_abort_if_unique_id_configured")
    ):
        result = await flow.async_step_user(user_input)
        
        assert result["type"] == "create_entry"
        assert result["title"] == "camera.doorbell"
        assert result["data"] == user_input

@pytest.mark.asyncio
async def test_flow_user_step_invalid_storage(flow):
    """Test when storage path is not writable."""
    user_input = {
        CONF_CAMERA_ENTITY_ID: "camera.doorbell",
        CONF_STORAGE_ROOT: "/invalid/path",
        CONF_FFMPEG_PATH: "ffmpeg",
    }
    
    with (
        patch("os.path.exists", return_value=True),
        patch("os.access", return_value=False)
    ):
        result = await flow.async_step_user(user_input)
        
        assert result["type"] == "form"
        assert result["errors"][CONF_STORAGE_ROOT] == "invalid_storage_path"

@pytest.mark.asyncio
async def test_flow_user_step_storage_makedirs_fail(flow):
    """Test when storage path doesn't exist and cannot be created."""
    user_input = {
        CONF_CAMERA_ENTITY_ID: "camera.doorbell",
        CONF_STORAGE_ROOT: "/restricted/path",
        CONF_FFMPEG_PATH: "ffmpeg",
    }
    
    with (
        patch("os.path.exists", return_value=False),
        patch("os.makedirs", side_effect=OSError)
    ):
        result = await flow.async_step_user(user_input)
        
        assert result["type"] == "form"
        assert result["errors"][CONF_STORAGE_ROOT] == "invalid_storage_path"

@pytest.mark.asyncio
async def test_flow_user_step_ffmpeg_not_found(flow):
    """Test when ffmpeg binary is not found."""
    user_input = {
        CONF_CAMERA_ENTITY_ID: "camera.doorbell",
        CONF_STORAGE_ROOT: "/tmp/video",
        CONF_FFMPEG_PATH: "missing_ffmpeg",
    }
    
    with (
        patch("os.path.exists", return_value=True),
        patch("os.access", return_value=True),
        patch("shutil.which", return_value=None)
    ):
        result = await flow.async_step_user(user_input)
        
        assert result["type"] == "form"
        assert result["errors"][CONF_FFMPEG_PATH] == "ffmpeg_not_found"
