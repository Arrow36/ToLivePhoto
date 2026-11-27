"""Tests for v2.2 & v2.3 features: custom-folder EXIF, blank default EXIF, _2live naming, & ByteDance font."""
from __future__ import annotations

import uuid
from pathlib import Path

from PIL import Image

from tolivephoto.core.apple_live import inspect_live_image, write_live_image
from tolivephoto.core.exif_io import find_matching_sidecar_exif_file, read_exif_from_file
from tolivephoto.core.models import (
    IPHONE_LIVE_BITRATE_MBPS,
    ConversionJob,
    CropRect,
    resolve_folder_output_paths,
)


def test_find_matching_sidecar_exif_in_custom_folder_and_subdir(tmp_path: Path) -> None:
    vid_dir = tmp_path / "videos"
    raw_root = tmp_path / "raw_shoot"
    raw_sub = raw_root / "Day1"
    vid_dir.mkdir()
    raw_sub.mkdir(parents=True)

    video = vid_dir / "CSI_7607.mp4"
    video.write_bytes(b"")
    assert find_matching_sidecar_exif_file(video) is None

    cr3 = raw_sub / "CSI_7607.CR3"
    cr3.write_bytes(b"")
    assert find_matching_sidecar_exif_file(video, search_dir=raw_root) == cr3


def test_bytedance_font_and_windowless_bat_present() -> None:
    root = Path(__file__).resolve().parent.parent
    font_file = root / "tolivephoto" / "assets" / "fonts" / "DouyinSansBold.ttf"
    assert font_file.is_file() and font_file.stat().st_size > 1_000_000

    bat_text = (root / "启动_ToLivePhoto.bat").read_text(encoding="utf-8", errors="ignore")
    assert "pythonw" in bat_text


def test_default_exif_is_blank_while_preserving_apple_makernote(tmp_path: Path) -> None:
    asset_id = str(uuid.uuid4()).upper()
    img = Image.new("RGB", (320, 480), (50, 90, 130))
    out_heic = tmp_path / "BLANK_EXIF.HEIC"
    write_live_image(img, out_heic, asset_id, custom_exif=None)

    assert inspect_live_image(out_heic) == asset_id
    meta = read_exif_from_file(out_heic)
    assert meta.make == ""
    assert meta.model == ""
    assert meta.software == ""


def test_folder_naming_modes_and_collision_avoidance(tmp_path: Path) -> None:
    assert IPHONE_LIVE_BITRATE_MBPS == 8.0

    job_suffix = ConversionJob(
        video_path=Path("CSI_7607.mp4"),
        start_frame=0,
        end_frame=10,
        crop=CropRect(0, 0, 100, 100),
    )
    img_p, mov_p, _, stem = resolve_folder_output_paths(tmp_path, "CSI_7607", "HEIC", job_suffix)
    assert stem == "CSI_7607_2live"
    assert img_p.name == "CSI_7607_2live.HEIC"
    assert mov_p.name == "CSI_7607_2live.MOV"

    img_p.write_bytes(b"x")
    img_p2, _, _, stem2 = resolve_folder_output_paths(tmp_path, "CSI_7607", "HEIC", job_suffix)
    assert stem2 == "CSI_7607_2live_1"
    assert img_p2.name == "CSI_7607_2live_1.HEIC"

    job_seq = ConversionJob(
        video_path=Path("CSI_7607.mp4"),
        start_frame=0,
        end_frame=10,
        crop=CropRect(0, 0, 100, 100),
        folder_naming_mode="prefix_seq",
        folder_custom_prefix="LIVE_",
        folder_seq_number=5,
    )
    _, _, _, stem_seq = resolve_folder_output_paths(tmp_path, "CSI_7607", "HEIC", job_seq)
    assert stem_seq == "LIVE_0005"
