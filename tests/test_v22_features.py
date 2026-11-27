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
    compute_polaroid_layout,
    preview_output_filename,
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


def test_polaroid_custom_crop_and_preserve_scale() -> None:
    custom = CropRect(200, 300, 900, 1500)
    crop_out, pad_out = compute_polaroid_layout("custom_crop", 2160, 3240, current_crop=custom)
    assert crop_out == custom
    assert pad_out.bottom > pad_out.top > 0
    assert pad_out.left == pad_out.right > 0

    crop_mini, pad_mini = compute_polaroid_layout(
        "mini", 2160, 3240, current_crop=CropRect(400, 400, 1200, 1200), preserve_crop_scale=True
    )
    assert crop_mini.w <= 1200 and crop_mini.h <= 1200
    assert pad_mini.bottom > pad_mini.top > 0


def test_auto_same_dir_exif_load_on_sidecar_jpg(tmp_path: Path) -> None:
    import os
    from PySide6.QtWidgets import QApplication
    from tolivephoto.core.exif_io import ExifMetadata
    from tolivephoto.ui.main_window import MainWindow

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _app = QApplication.instance() or QApplication([])

    video = tmp_path / "CLIP_0001.mp4"
    video.write_bytes(b"")
    sidecar = tmp_path / "CLIP_0001.jpg"
    img = Image.new("RGB", (64, 64), (10, 20, 30))
    exif = img.getexif()
    exif[0x010F] = "Canon"
    exif[0x0110] = "EOS R5"
    img.save(sidecar, format="JPEG", exif=exif)

    win = MainWindow()
    meta = win._auto_load_same_dir_exif(video)
    assert isinstance(meta, ExifMetadata)
    assert meta.make == "Canon"
    assert meta.model == "EOS R5"

    no_sidecar = tmp_path / "CLIP_9999.mp4"
    no_sidecar.write_bytes(b"")
    assert win._auto_load_same_dir_exif(no_sidecar) is None
    win.close()


def test_dcim_default_2live_and_modern_naming(tmp_path: Path) -> None:
    from tolivephoto.core.models import VideoInfo

    info = VideoInfo(
        path=Path("CSI_7607.mp4"),
        width=2160,
        height=3240,
        fps_num=24,
        fps_den=1,
        fps=24.0,
        nb_frames=59,
        duration=59 / 24.0,
        has_audio=True,
    )
    job = ConversionJob(
        video_path=info.path,
        start_frame=0,
        end_frame=58,
        crop=CropRect(0, 0, 2160, 3240),
        export_mode="usb_dcim",
        output_root=tmp_path,
    )
    assert job.dcf_number is None
    assert preview_output_filename(info, job) == "DCIM/100APPLE/CSI_7607_2live.HEIC + .MOV"

    dcim_dir = tmp_path / "DCIM" / "100APPLE"
    img_p, mov_p, _, stem = resolve_folder_output_paths(dcim_dir, info.path.stem, "HEIC", job)
    assert stem == "CSI_7607_2live"
    assert img_p == dcim_dir / "CSI_7607_2live.HEIC"
    assert mov_p == dcim_dir / "CSI_7607_2live.MOV"


def test_custom_app_icon_assets_and_qicon() -> None:
    import os
    from PySide6.QtWidgets import QApplication
    from tolivephoto.ui.icon import ICON_ICO_PATH, ICON_PNG_PATH, create_app_icon

    assert ICON_PNG_PATH.is_file() and ICON_PNG_PATH.stat().st_size > 10_000
    assert ICON_ICO_PATH.is_file() and ICON_ICO_PATH.stat().st_size > 5_000

    with Image.open(ICON_PNG_PATH) as im:
        assert im.size == (512, 512)
        assert im.mode == "RGBA"
        assert im.getpixel((0, 0))[3] == 0
        assert im.getpixel((256, 256))[3] == 255

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _app = QApplication.instance() or QApplication([])
    icon = create_app_icon()
    assert not icon.pixmap(64, 64).isNull()


def test_cancellable_run_command_kills_subprocess_immediately() -> None:
    import sys
    import time
    import pytest
    from tolivephoto.core.tools import ConversionCancelled, run_command

    t0 = time.monotonic()
    with pytest.raises(ConversionCancelled):
        run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cancel_cb=lambda: (time.monotonic() - t0) >= 0.15,
        )
    elapsed = time.monotonic() - t0
    assert elapsed < 3.0


def test_rife_v425_high_res_threads_no_black_frames(tmp_path: Path) -> None:
    import cv2
    import numpy as np
    import pytest
    from tolivephoto.core.interpolator import interpolate_frames_rife
    from tolivephoto.core.tools import find_rife

    if find_rife() is None:
        pytest.skip("RIFE not installed")

    inp = tmp_path / "in"
    out = tmp_path / "out"
    inp.mkdir()
    img = np.full((2996, 2508, 3), 130, dtype=np.uint8)
    for i in range(1, 5):
        cv2.imwrite(str(inp / f"{i:08d}.jpg"), img)

    interpolate_frames_rife(
        input_dir=inp,
        output_dir=out,
        target_frames=8,
        model_name="rife-v4.25",
        width=2508,
        height=2996,
        gpu_id=0,
        frame_ext="jpg",
    )
    produced = sorted(out.glob("*.jpg"))
    assert len(produced) == 8
    for p in produced:
        frame = cv2.imread(str(p))
        assert frame is not None and float(frame.mean()) >= 5.0
