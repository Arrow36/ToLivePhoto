"""Tests for out-of-bounds crop/pad with Gaussian blur and solid color backgrounds."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from tolivephoto.core.apple_live import verify_live_pair
from tolivephoto.core.models import ConversionJob, CropRect
from tolivephoto.core.pipeline import compose_cropped_frame_rgb, run_conversion
from tolivephoto.core.tools import find_ffmpeg, run_command


def test_compose_cropped_frame_solid_and_blur() -> None:
    rgb = np.zeros((100, 100, 3), dtype=np.uint8)
    rgb[:, :] = (20, 100, 220)
    crop = CropRect(-50, 0, 200, 100)

    solid_out = compose_cropped_frame_rgb(rgb, crop, bg_mode="solid", bg_color="#FF0000")
    assert solid_out.shape == (100, 200, 3)
    assert tuple(solid_out[50, 10]) == (255, 0, 0)
    assert tuple(solid_out[50, 100]) == (20, 100, 220)

    blur_out = compose_cropped_frame_rgb(rgb, crop, bg_mode="blur", bg_blur_sigma=30)
    assert blur_out.shape == (100, 200, 3)
    assert blur_out[50, 10, 2] > 180


def test_out_of_bounds_video_and_cover_pipeline(tmp_path: Path) -> None:
    ffmpeg = find_ffmpeg()
    src_mp4 = tmp_path / "src.mp4"
    run_command(
        [
            str(ffmpeg),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x2266CC:s=320x480:r=24:d=1.0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(src_mp4),
        ]
    )

    job = ConversionJob(
        video_path=src_mp4,
        start_frame=0,
        end_frame=23,
        crop=CropRect(-80, 0, 480, 480),
        mute=True,
        fps_mode="keep_original",
        key_source="video_frame",
        key_frame_index=12,
        export_mode="folder",
        output_root=tmp_path / "out",
        folder_stem="PadTest",
        image_format="heic",
        video_max_edge=0,
        use_hw_encode=False,
        bg_mode="solid",
        bg_color="#FFFFFF",
    )
    res = run_conversion(job)
    assert res.jpg_path.suffix == ".HEIC"
    assert res.jpg_path.is_file() and res.mov_path.is_file()
    verify_live_pair(res.jpg_path, res.mov_path)

    with Image.open(res.jpg_path) as im:
        assert im.size == (480, 480)
        px = im.convert("RGB").getpixel((10, 240))
        assert px[0] > 240 and px[1] > 240 and px[2] > 240
