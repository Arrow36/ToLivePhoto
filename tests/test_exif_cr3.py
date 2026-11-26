"""Tests for EXIF reader (.CR3 / JPEG) and Apple Live Photo MakerNote + camera EXIF preservation."""
from __future__ import annotations

import struct
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from tolivephoto.core.apple_live import inspect_live_image, write_live_image
from tolivephoto.core.exif_io import ExifMetadata, read_exif_from_file


def _pack_le_ifd(entries: list[tuple[int, int, int, bytes]], base_offset: int) -> bytes:
    entries = sorted(entries, key=lambda x: x[0])
    ifd_sz = 2 + len(entries) * 12 + 4
    cursor = base_offset + ifd_sz
    head = bytearray(struct.pack("<H", len(entries)))
    data = bytearray()
    for tag, typ, cnt, raw in entries:
        if len(raw) <= 4:
            head.extend(struct.pack("<HHI", tag, typ, cnt) + raw.ljust(4, b"\x00"))
        else:
            head.extend(struct.pack("<HHII", tag, typ, cnt, cursor))
            data.extend(raw)
            if len(raw) % 2:
                data.append(0)
            cursor = base_offset + ifd_sz + len(data)
    head.extend(struct.pack("<I", 0))
    return bytes(head) + bytes(data)


def _make_le_tiff(entries: list[tuple[int, int, int, bytes]]) -> bytes:
    return b"II*\x00" + struct.pack("<I", 8) + _pack_le_ifd(entries, base_offset=8)


def _cr3_box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), box_type) + payload


def test_synthetic_cr3_exif_read_and_heic_jpg_write(tmp_path: Path) -> None:
    cmt1 = _make_le_tiff(
        [
            (0x010F, 2, 6, b"Canon\x00"),
            (0x0110, 2, 18, b"Canon EOS R5 Test\x00"),
        ]
    )
    cmt2 = _make_le_tiff(
        [
            (0x829A, 5, 1, struct.pack("<II", 1, 250)),
            (0x829D, 5, 1, struct.pack("<II", 28, 10)),
            (0x8827, 3, 1, struct.pack("<H", 400)),
            (0x9003, 2, 20, b"2026:11:26 16:21:58\x00"),
            (0x9011, 2, 7, b"+08:00\x00"),
            (0x920A, 5, 1, struct.pack("<II", 50, 1)),
            (0xA434, 2, 14, b"RF 50mm F1.2L\x00"),
        ]
    )
    cr3_path = tmp_path / "sample.CR3"
    cr3_bytes = _cr3_box(b"ftyp", b"crx \x00\x00\x00\x00crx ") + _cr3_box(b"CMT1", cmt1) + _cr3_box(b"CMT2", cmt2)
    cr3_path.write_bytes(cr3_bytes)

    meta = read_exif_from_file(cr3_path)
    assert meta.make == "Canon"
    assert meta.model == "Canon EOS R5 Test"
    assert meta.lens_model == "RF 50mm F1.2L"
    assert meta.exposure_time == "1/250"
    assert meta.f_number == "2.8"
    assert meta.iso == 400
    assert meta.focal_length == "50"
    assert meta.datetime_original == "2026:11:26 16:21:58"

    # Write HEIC and JPG with this custom EXIF AND verify Apple MakerNote UUID survives!
    asset_id = str(uuid.uuid4()).upper()
    img = Image.new("RGB", (320, 480), (120, 140, 180))
    out_heic = tmp_path / "OUT_0001.HEIC"
    out_jpg = tmp_path / "OUT_0001.JPG"
    write_live_image(img, out_heic, asset_id, creation_dt=datetime(2026, 11, 26, 16, 21, 58), custom_exif=meta)
    write_live_image(img, out_jpg, asset_id, creation_dt=datetime(2026, 11, 26, 16, 21, 58), custom_exif=meta)

    assert inspect_live_image(out_heic) == asset_id
    assert inspect_live_image(out_jpg) == asset_id

    read_back_heic = read_exif_from_file(out_heic)
    assert read_back_heic.make == "Canon"
    assert read_back_heic.model == "Canon EOS R5 Test"
    assert read_back_heic.exposure_time == "1/250"
    assert read_back_heic.iso == 400

    read_back_jpg = read_exif_from_file(out_jpg)
    assert read_back_jpg.make == "Canon"
    assert read_back_jpg.lens_model == "RF 50mm F1.2L"


OPTIONAL_CR3 = Path.home() / "Desktop" / "2026_11_26_道前街银杏" / "CSI_7607.CR3"


@pytest.mark.skipif(not OPTIONAL_CR3.is_file(), reason="Optional Desktop CR3 sample not present")
def test_optional_real_canon_cr3_exif() -> None:
    meta = read_exif_from_file(OPTIONAL_CR3)
    assert meta.make == "Canon"
    assert "G5 X Mark II" in meta.model
    assert meta.exposure_time == "1/250"
    assert meta.f_number == "5.6"
    assert meta.iso == 125
    assert meta.datetime_original.startswith("2026:11:26")
