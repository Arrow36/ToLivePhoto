"""Unit tests for USB DCIM/100APPLE custom DCF prefix/number, auto-increment, and C: exclusion."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from tolivephoto.core.usb_export import (
    extract_4digit_seed,
    list_usb_drives,
    normalize_dcf_prefix,
    resolve_dcf_pair_paths,
    sync_pair_timestamps,
)


def test_extract_4digit_seed_and_prefix() -> None:
    assert extract_4digit_seed("CSI_7607") == 7607
    assert extract_4digit_seed("VID_20261126_0042") == 42
    assert extract_4digit_seed("clip") == 1
    assert normalize_dcf_prefix("csi") == "CSI_"
    assert normalize_dcf_prefix("LIVE") == "LIVE"
    assert normalize_dcf_prefix("") == "IMG_"


def test_dcf_custom_prefix_and_collision_increment(tmp_path: Path) -> None:
    img1, mov1, stem1, num1 = resolve_dcf_pair_paths(
        tmp_path, preferred_number=7, source_stem="CSI_7607", image_ext="HEIC", prefix="CSI_"
    )
    assert stem1 == "CSI_0007"
    assert num1 == 7
    assert img1 == tmp_path / "DCIM" / "100APPLE" / "CSI_0007.HEIC"
    assert mov1 == tmp_path / "DCIM" / "100APPLE" / "CSI_0007.MOV"

    img1.write_bytes(b"heic")
    mov1.write_bytes(b"mov")

    img2, mov2, stem2, num2 = resolve_dcf_pair_paths(
        tmp_path, preferred_number=7, source_stem="CSI_7607", image_ext="JPG", prefix="CSI_"
    )
    assert stem2 == "CSI_0008"
    assert num2 == 8
    assert img2 == tmp_path / "DCIM" / "100APPLE" / "CSI_0008.JPG"
    assert mov2 == tmp_path / "DCIM" / "100APPLE" / "CSI_0008.MOV"

    dt = datetime(2026, 11, 26, 22, 15, 0)
    sync_pair_timestamps(img1, mov1, dt)
    assert abs(img1.stat().st_mtime - mov1.stat().st_mtime) < 1e-3


def test_list_usb_drives_excludes_system_drive() -> None:
    sys_drive = (os.environ.get("SystemDrive") or "C:").rstrip("\\/").upper()
    drives = list_usb_drives(include_system_drive=False)
    for d in drives:
        assert str(d.root).rstrip("\\/").upper() != sys_drive
