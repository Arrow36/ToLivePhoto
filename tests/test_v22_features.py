"""Tests for v2.2 features: custom-folder EXIF matching, ByteDance font bundling, and windowless launcher."""
from __future__ import annotations

from pathlib import Path

from tolivephoto.core.exif_io import find_matching_sidecar_exif_file


def test_find_matching_sidecar_exif_in_custom_folder_and_subdir(tmp_path: Path) -> None:
    vid_dir = tmp_path / "videos"
    raw_root = tmp_path / "raw_shoot"
    raw_sub = raw_root / "Day1"
    vid_dir.mkdir()
    raw_sub.mkdir(parents=True)

    video = vid_dir / "CSI_7607.mp4"
    video.write_bytes(b"")

    # No sidecar beside video
    assert find_matching_sidecar_exif_file(video) is None

    # Sidecar placed inside a subdirectory of user-chosen folder
    cr3 = raw_sub / "CSI_7607.CR3"
    cr3.write_bytes(b"")
    assert find_matching_sidecar_exif_file(video, search_dir=raw_root) == cr3


def test_bytedance_font_and_windowless_bat_present() -> None:
    root = Path(__file__).resolve().parent.parent
    font_file = root / "tolivephoto" / "assets" / "fonts" / "DouyinSansBold.ttf"
    assert font_file.is_file() and font_file.stat().st_size > 1_000_000

    bat_text = (root / "启动_ToLivePhoto.bat").read_text(encoding="utf-8", errors="ignore")
    assert "pythonw" in bat_text
