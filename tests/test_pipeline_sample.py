"""End-to-end integration tests for ToLivePhoto default unscaled output & RIFE."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from tolivephoto.core.apple_live import verify_live_pair
from tolivephoto.core.iso_bmff import parse_atoms
from tolivephoto.core.models import ConversionJob, CropRect
from tolivephoto.core.pipeline import run_conversion
from tolivephoto.core.probe import probe_video
from tolivephoto.core.tools import find_ffprobe, find_rife, run_command

SAMPLE_VIDEO = Path.home() / "Desktop" / "CSI_7607.mp4"


def _ffprobe_json(path: Path) -> dict:
    ffprobe = find_ffprobe()
    proc = run_command(
        [
            str(ffprobe),
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    return json.loads(proc.stdout)


def test_default_video_max_edge_is_unscaled() -> None:
    job = ConversionJob(
        video_path=Path("dummy.mp4"),
        start_frame=0,
        end_frame=10,
        crop=CropRect(0, 0, 100, 100),
    )
    assert job.video_max_edge == 0
    assert job.image_format == "heic"
    assert job.dcf_prefix == "IMG_"


@pytest.mark.skipif(not SAMPLE_VIDEO.is_file(), reason="Optional Desktop sample video not present")
def test_unscaled_heic_raw_retime_custom_dcf(tmp_path: Path) -> None:
    info = probe_video(SAMPLE_VIDEO)
    assert info.width == 2160 and info.height == 3240
    assert info.nb_frames == 59

    job = ConversionJob(
        video_path=SAMPLE_VIDEO,
        start_frame=0,
        end_frame=58,
        crop=CropRect(0, 0, 2160, 3240),
        mute=True,
        fps_mode="raw_retime",
        custom_fps=25.0,
        key_source="video_frame",
        key_frame_index=20,
        export_mode="usb_dcim",
        output_root=tmp_path,
        dcf_prefix="CSI_",
        dcf_number=7607,
    )
    res = run_conversion(job)

    assert res.used_dcf_number == 7607
    assert res.jpg_path == tmp_path / "DCIM" / "100APPLE" / "CSI_7607.HEIC"
    assert res.mov_path == tmp_path / "DCIM" / "100APPLE" / "CSI_7607.MOV"

    with Image.open(res.jpg_path) as cov:
        assert cov.size == (2160, 3240)

    top_types = [a.type for a in parse_atoms(res.mov_path.read_bytes())]
    assert top_types == [b"ftyp", b"moov", b"mdat"]

    probe = _ffprobe_json(res.mov_path)
    streams = probe["streams"]
    assert all(s.get("codec_tag_string") != "tmcd" for s in streams)
    vstream = next(s for s in streams if s["codec_type"] == "video")
    assert vstream["width"] == 2160 and vstream["height"] == 3240
    assert vstream["nb_frames"] == "59"
    assert vstream["avg_frame_rate"] == "25/1"

    verify_live_pair(res.jpg_path, res.mov_path)


@pytest.mark.skipif(not SAMPLE_VIDEO.is_file() or find_rife() is None, reason="Sample video or RIFE missing")
def test_rife_2x_oob_blur_3_4_pad(tmp_path: Path) -> None:
    job = ConversionJob(
        video_path=SAMPLE_VIDEO,
        start_frame=0,
        end_frame=23,
        crop=CropRect(-135, 0, 2430, 3240),
        mute=False,
        fps_mode="rife_interpolate",
        rife_mode="multiplier",
        rife_multiplier=2,
        rife_model="rife-v4.25",
        rife_playback_policy="keep_duration",
        key_source="video_frame",
        key_frame_index=12,
        export_mode="folder",
        output_root=tmp_path / "folder_out",
        folder_stem="TestRifeBlur",
        video_max_edge=1920,
        bg_mode="blur",
        bg_blur_sigma=35,
    )
    res = run_conversion(job)

    assert res.jpg_path.is_file() and res.mov_path.is_file()
    probe = _ffprobe_json(res.mov_path)
    vstream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert vstream["width"] == 1440 and vstream["height"] == 1920
    assert vstream["nb_frames"] == "48"
    assert vstream["avg_frame_rate"] == "48/1"
    verify_live_pair(res.jpg_path, res.mov_path)
