"""Unit tests for Big-Endian Apple Live Photo HEIC/JPEG EXIF & QuickTime MOV atom muxing."""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from PIL import Image

from tolivephoto.core.apple_live import (
    CONTENT_KEY,
    STILL_TIME_KEY,
    apple_maker_note,
    build_apple_exif_bytes,
    inspect_live_image,
    inspect_live_mov,
    verify_live_pair,
    write_live_image,
    write_live_mov,
)
from tolivephoto.core.iso_bmff import parse_atoms
from tolivephoto.core.tools import find_ffmpeg, run_command


def test_apple_makernote_big_endian_heic_and_jpg(tmp_path: Path) -> None:
    asset_id = str(uuid.uuid4()).upper()
    note = apple_maker_note(asset_id)
    assert note.startswith(b"Apple iOS\x00\x00\x01MM")
    assert asset_id.encode("ascii") in note

    exif_bytes = build_apple_exif_bytes(asset_id, 320, 480, datetime(2026, 11, 26, 22, 0, 0))
    assert exif_bytes.startswith(b"Exif\x00\x00MM\x00*")

    src = Image.new("RGB", (320, 480), (40, 120, 200))
    out_heic = tmp_path / "IMG_0001.HEIC"
    out_jpg = tmp_path / "IMG_0001.JPG"
    write_live_image(src, out_heic, asset_id, creation_dt=datetime(2026, 11, 26, 22, 0, 0))
    write_live_image(src, out_jpg, asset_id, creation_dt=datetime(2026, 11, 26, 22, 0, 0))

    assert inspect_live_image(out_heic) == asset_id
    assert inspect_live_image(out_jpg) == asset_id


def test_apple_mov_moov_first_and_live_photo_keys(tmp_path: Path) -> None:
    ffmpeg = find_ffmpeg()
    raw_mov = tmp_path / "raw.mov"
    run_command(
        [
            str(ffmpeg),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:r=24:d=1.5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-f",
            "mov",
            str(raw_mov),
        ]
    )

    asset_id = str(uuid.uuid4()).upper()
    out_heic = tmp_path / "IMG_1234.HEIC"
    out_mov = tmp_path / "IMG_1234.MOV"
    write_live_image(Image.new("RGB", (320, 240), (10, 20, 30)), out_heic, asset_id)
    write_live_mov(raw_mov, out_mov, asset_id, still_time=0.75, duration=1.5)

    raw_bytes = out_mov.read_bytes()
    top_types = [a.type for a in parse_atoms(raw_bytes)]
    assert top_types == [b"ftyp", b"moov", b"mdat"]
    assert CONTENT_KEY in raw_bytes
    assert STILL_TIME_KEY in raw_bytes
    assert b"com.apple.quicktime.live-photo.auto" in raw_bytes
    assert b"com.apple.quicktime.live-photo.vitality-score" in raw_bytes

    mov_info = inspect_live_mov(out_mov)
    assert mov_info.asset_id == asset_id
    assert abs(mov_info.still_time - 0.75) < 0.01

    verified = verify_live_pair(out_heic, out_mov)
    assert verified["asset_id"] == asset_id
