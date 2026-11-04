from pathlib import Path

from PIL import Image


EXPECTED_ICONS = {
    Path("icon.png"): (256, 256),
    Path("custom_components/xiaomi_doorbell_m3u8_archiver/brand/icon.png"): (256, 256),
    Path("custom_components/xiaomi_doorbell_m3u8_archiver/brand/icon@2x.png"): (512, 512),
    Path("custom_components/xiaomi_doorbell_m3u8_archiver/brand/logo.png"): (256, 256),
    Path("custom_components/xiaomi_doorbell_m3u8_archiver/brand/logo@2x.png"): (512, 512),
}


def test_brand_icons_are_transparent_pngs_with_expected_sizes() -> None:
    for path, expected_size in EXPECTED_ICONS.items():
        with Image.open(path) as image:
            assert image.format == "PNG"
            assert image.size == expected_size
            assert image.mode == "RGBA"
            assert image.getchannel("A").getextrema() == (0, 255)
