"""Pure-Python Apple Live Photo (Big-Endian EXIF/HEIC MakerNote + QuickTime MOV atoms) muxer and verifier."""
from __future__ import annotations

import re
import struct
import uuid
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from PIL import Image
from pillow_heif import register_heif_opener

from .exif_io import ExifMetadata
from .iso_bmff import Atom, atom, child_atoms, first_child, parse_atoms, patch_chunk_offsets

register_heif_opener()

CONTENT_KEY = b"com.apple.quicktime.content.identifier"
STILL_TIME_KEY = b"com.apple.quicktime.still-image-time"
TIMED_SAMPLE = b"\x00\x00\x00\x09\x00\x00\x00\x01\x00"

DEVICE_MAKE = "Apple"
DEVICE_MODEL = "iPhone 15 Pro"
DEVICE_SOFTWARE = "18.0"


@dataclass(frozen=True, slots=True)
class LiveMovInfo:
    asset_id: str
    still_time: float
    video_track_id: int
    metadata_track_id: int
    video_chunk_offset: int
    metadata_chunk_offset: int
    sample_value: int


def canonical_asset_id(asset_id: str) -> str:
    try:
        return str(uuid.UUID(asset_id)).upper()
    except (ValueError, AttributeError) as exc:
        raise ValueError("Apple asset identifier must be a valid UUID") from exc


def apple_maker_note(asset_id: str) -> bytes:
    """Build Apple iOS MakerNote IFD with MakerNoteVersion, ContentIdentifier (0x0011), ImageCaptureType, ImageUniqueID."""
    identifier = canonical_asset_id(asset_id).encode("ascii") + b"\x00"
    header = b"Apple iOS\x00\x00\x01MM"
    count = 4
    data_start = 14 + 2 + count * 12 + 4

    id_offset = data_start
    padded_id = identifier + (b"\x00" if len(identifier) % 2 else b"")
    uniq_offset = id_offset + len(padded_id)

    entries = b"".join(
        [
            struct.pack(">HHII", 0x0001, 9, 1, 14),
            struct.pack(">HHII", 0x0011, 2, len(identifier), id_offset),
            struct.pack(">HHII", 0x0014, 9, 1, 10),
            struct.pack(">HHII", 0x0015, 2, len(identifier), uniq_offset),
        ]
    )
    return header + struct.pack(">H", count) + entries + struct.pack(">I", 0) + padded_id + padded_id


def _encode_tiff_ifd(entries: list[tuple[int, int, int, bytes]], base_offset: int) -> tuple[bytes, bytes]:
    entries_sorted = sorted(entries, key=lambda item: item[0])
    ifd_size = 2 + len(entries_sorted) * 12 + 4
    data_cursor = base_offset + ifd_size
    ifd_buf = bytearray(struct.pack(">H", len(entries_sorted)))
    data_buf = bytearray()

    for tag, typ, count, raw_val in entries_sorted:
        if len(raw_val) <= 4:
            padded = raw_val.ljust(4, b"\x00")
            ifd_buf.extend(struct.pack(">HHI", tag, typ, count) + padded)
        else:
            ifd_buf.extend(struct.pack(">HHII", tag, typ, count, data_cursor))
            data_buf.extend(raw_val)
            if len(raw_val) % 2:
                data_buf.append(0)
            data_cursor = base_offset + ifd_size + len(data_buf)

    ifd_buf.extend(struct.pack(">I", 0))
    return bytes(ifd_buf), bytes(data_buf)


def _ascii_bytes(text: str) -> bytes:
    return text.encode("utf-8", errors="replace") + b"\x00"


def _parse_positive_rational(text: str) -> tuple[int, int] | None:
    raw = text.strip().lower().rstrip("s").lstrip("f/")
    if not raw:
        return None
    try:
        frac = Fraction(raw).limit_denominator(1_000_000)
    except (ValueError, ZeroDivisionError):
        return None
    if frac <= 0:
        return None
    return int(frac.numerator), int(frac.denominator)


def _parse_signed_rational(text: str) -> tuple[int, int] | None:
    raw = text.strip()
    if not raw:
        return None
    try:
        frac = Fraction(raw).limit_denominator(10_000)
    except (ValueError, ZeroDivisionError):
        return None
    return int(frac.numerator), int(frac.denominator)


def _deg_to_dms_rational_bytes(deg_abs: float) -> bytes:
    d = int(deg_abs)
    rem_min = (deg_abs - d) * 60.0
    m = int(rem_min)
    s_num = int(round((rem_min - m) * 60.0 * 10000))
    return struct.pack(">IIIIII", d, 1, m, 1, s_num, 10000)


def build_apple_exif_bytes(
    asset_id: str,
    width: int,
    height: int,
    creation_dt: datetime | None = None,
    custom_exif: ExifMetadata | None = None,
) -> bytes:
    """Build Big-Endian (MM) EXIF payload with IFD0 + Exif SubIFD (0x8769) + Apple MakerNote + optional camera/GPS tags."""
    dt = (custom_exif.parsed_datetime() if custom_exif else None) or creation_dt or datetime.now()
    dt_str = dt.strftime("%Y:%m:%d %H:%M:%S")
    dt_bytes = dt_str.encode("ascii") + b"\x00"

    make_str = custom_exif.make.strip() if custom_exif else ""
    model_str = custom_exif.model.strip() if custom_exif else ""
    sw_str = custom_exif.software.strip() if custom_exif else ""

    note_b = apple_maker_note(asset_id)

    has_gps = bool(custom_exif and custom_exif.gps_lat is not None and custom_exif.gps_lon is not None)

    ifd0_base: list[tuple[int, int, int, bytes]] = [
        (0x0112, 3, 1, struct.pack(">H", 1)),
        (0x0132, 2, len(dt_bytes), dt_bytes),
    ]
    if make_str:
        make_b = _ascii_bytes(make_str)
        ifd0_base.append((0x010F, 2, len(make_b), make_b))
    if model_str:
        model_b = _ascii_bytes(model_str)
        ifd0_base.append((0x0110, 2, len(model_b), model_b))
    if sw_str:
        sw_b = _ascii_bytes(sw_str)
        ifd0_base.append((0x0131, 2, len(sw_b), sw_b))
    if custom_exif and custom_exif.artist.strip():
        art_b = _ascii_bytes(custom_exif.artist.strip())
        ifd0_base.append((0x013B, 2, len(art_b), art_b))
    if custom_exif and custom_exif.copyright.strip():
        cpr_b = _ascii_bytes(custom_exif.copyright.strip())
        ifd0_base.append((0x8298, 2, len(cpr_b), cpr_b))

    ifd0_stub = list(ifd0_base) + [(0x8769, 4, 1, struct.pack(">I", 0))]
    if has_gps:
        ifd0_stub.append((0x8825, 4, 1, struct.pack(">I", 0)))

    ifd0_bytes_0, data0_bytes_0 = _encode_tiff_ifd(ifd0_stub, base_offset=8)
    sub_ifd_offset = 8 + len(ifd0_bytes_0) + len(data0_bytes_0)

    sub_entries: list[tuple[int, int, int, bytes]] = [
        (0x9003, 2, len(dt_bytes), dt_bytes),
        (0x9004, 2, len(dt_bytes), dt_bytes),
        (0x927C, 7, len(note_b), note_b),
        (0xA002, 4, 1, struct.pack(">I", int(width))),
        (0xA003, 4, 1, struct.pack(">I", int(height))),
    ]
    if custom_exif:
        if custom_exif.offset_time.strip():
            off_b = _ascii_bytes(custom_exif.offset_time.strip())
            sub_entries.append((0x9010, 2, len(off_b), off_b))
            sub_entries.append((0x9011, 2, len(off_b), off_b))
        exp = _parse_positive_rational(custom_exif.exposure_time)
        if exp:
            sub_entries.append((0x829A, 5, 1, struct.pack(">II", *exp)))
        fnum = _parse_positive_rational(custom_exif.f_number)
        if fnum:
            sub_entries.append((0x829D, 5, 1, struct.pack(">II", *fnum)))
        if custom_exif.iso is not None and custom_exif.iso > 0:
            sub_entries.append((0x8827, 3, 1, struct.pack(">H", min(65535, int(custom_exif.iso)))))
        ev = _parse_signed_rational(custom_exif.exposure_bias)
        if ev is not None and custom_exif.exposure_bias.strip():
            sub_entries.append((0x9204, 10, 1, struct.pack(">ii", *ev)))
        fl = _parse_positive_rational(custom_exif.focal_length)
        if fl:
            sub_entries.append((0x920A, 5, 1, struct.pack(">II", *fl)))
        if custom_exif.focal_length_35mm is not None and custom_exif.focal_length_35mm > 0:
            sub_entries.append((0xA405, 3, 1, struct.pack(">H", min(65535, int(custom_exif.focal_length_35mm)))))
        if custom_exif.lens_model.strip():
            lens_b = _ascii_bytes(custom_exif.lens_model.strip())
            sub_entries.append((0xA434, 2, len(lens_b), lens_b))

    sub_bytes, sub_data = _encode_tiff_ifd(sub_entries, base_offset=sub_ifd_offset)

    gps_bytes = b""
    gps_data = b""
    ifd0_real = list(ifd0_base) + [(0x8769, 4, 1, struct.pack(">I", sub_ifd_offset))]
    if has_gps and custom_exif is not None and custom_exif.gps_lat is not None and custom_exif.gps_lon is not None:
        gps_offset = sub_ifd_offset + len(sub_bytes) + len(sub_data)
        ifd0_real.append((0x8825, 4, 1, struct.pack(">I", gps_offset)))
        lat_ref = b"N\x00" if custom_exif.gps_lat >= 0 else b"S\x00"
        lon_ref = b"E\x00" if custom_exif.gps_lon >= 0 else b"W\x00"
        gps_entries = [
            (0x0001, 2, 2, lat_ref),
            (0x0002, 5, 3, _deg_to_dms_rational_bytes(abs(custom_exif.gps_lat))),
            (0x0003, 2, 2, lon_ref),
            (0x0004, 5, 3, _deg_to_dms_rational_bytes(abs(custom_exif.gps_lon))),
        ]
        gps_bytes, gps_data = _encode_tiff_ifd(gps_entries, base_offset=gps_offset)

    ifd0_bytes, data0_bytes = _encode_tiff_ifd(ifd0_real, base_offset=8)
    tiff = b"MM\x00*" + struct.pack(">I", 8) + ifd0_bytes + data0_bytes + sub_bytes + sub_data + gps_bytes + gps_data
    return b"Exif\x00\x00" + tiff


def write_live_image(
    source: Image.Image | Path,
    output: Path,
    asset_id: str,
    creation_dt: datetime | None = None,
    custom_exif: ExifMetadata | None = None,
) -> None:
    """Write HEIC or JPEG cover image with Big-Endian Apple EXIF + MakerNote ContentIdentifier."""
    should_close = False
    if isinstance(source, Path):
        original = Image.open(source)
        should_close = True
    else:
        original = source

    try:
        icc_profile = original.info.get("icc_profile") if hasattr(original, "info") else None
        image = original.convert("RGB")
        exif_bytes = build_apple_exif_bytes(
            asset_id,
            image.width,
            image.height,
            creation_dt=creation_dt,
            custom_exif=custom_exif,
        )

        ext = output.suffix.lower()
        if ext in (".heic", ".heif"):
            save_args: dict[str, object] = {
                "format": "HEIF",
                "quality": 92,
                "exif": exif_bytes,
            }
        else:
            save_args = {
                "format": "JPEG",
                "quality": 96,
                "subsampling": 0,
                "exif": exif_bytes,
            }
        if icc_profile:
            save_args["icc_profile"] = icc_profile
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, **save_args)
    finally:
        if should_close:
            original.close()


write_live_jpeg = write_live_image


def _extract_makernote_from_tiff(tiff: bytes) -> bytes | None:
    if tiff.startswith(b"Exif\x00\x00"):
        tiff = tiff[6:]
    if len(tiff) < 8:
        return None
    if tiff[:4] == b"MM\x00*":
        endian = ">"
    elif tiff[:4] == b"II*\x00":
        endian = "<"
    else:
        return None

    def read_ifd(offset: int) -> dict[int, tuple[int, int, int, bytes]]:
        if offset + 2 > len(tiff):
            return {}
        cnt = struct.unpack(endian + "H", tiff[offset : offset + 2])[0]
        out: dict[int, tuple[int, int, int, bytes]] = {}
        for i in range(cnt):
            ep = offset + 2 + i * 12
            if ep + 12 > len(tiff):
                break
            tag, typ, count = struct.unpack(endian + "HHI", tiff[ep : ep + 8])
            raw_4 = tiff[ep + 8 : ep + 12]
            val_off = struct.unpack(endian + "I", raw_4)[0]
            out[tag] = (typ, count, val_off, raw_4)
        return out

    ifd0_off = struct.unpack(endian + "I", tiff[4:8])[0]
    ifd0 = read_ifd(ifd0_off)
    if 0x8769 in ifd0:
        sub_off = ifd0[0x8769][2]
        sub = read_ifd(sub_off)
        if 0x927C in sub:
            _typ, cnt, val_off, _ = sub[0x927C]
            return tiff[val_off : val_off + cnt]
    if 0x927C in ifd0:
        _typ, cnt, val_off, _ = ifd0[0x927C]
        return tiff[val_off : val_off + cnt]
    return None


def inspect_live_image(path: Path) -> str:
    """Read and validate Apple MakerNote ContentIdentifier from HEIC or JPEG."""
    with Image.open(path) as image:
        raw_exif = image.info.get("exif")
        note = _extract_makernote_from_tiff(raw_exif) if isinstance(raw_exif, (bytes, bytearray)) else None
        if note is None:
            exif = image.getexif()
            sub_ifd = exif.get_ifd(0x8769)
            note = sub_ifd.get(0x927C) if sub_ifd else None
            if note is None:
                note = exif.get(0x927C)

    if not isinstance(note, bytes) or not note.startswith(b"Apple iOS\x00\x00\x01MM"):
        raise ValueError("图片缺少 Apple MakerNote")
    cnt = struct.unpack(">H", note[14:16])[0]
    for i in range(cnt):
        ep = 16 + i * 12
        if ep + 12 > len(note):
            break
        tag, typ, count, offset = struct.unpack(">HHII", note[ep : ep + 12])
        if tag == 0x0011 and typ == 2:
            raw = note[offset : offset + count].rstrip(b"\x00")
            return canonical_asset_id(raw.decode("ascii"))
    raise ValueError("Apple MakerNote 缺少内容标识 tag 0x0011")


inspect_live_jpeg = inspect_live_image


def _fullbox(version: int = 0, flags: int = 0) -> bytes:
    return bytes((version,)) + flags.to_bytes(3, "big")


def _pascal(text: bytes) -> bytes:
    if len(text) > 255:
        raise ValueError("Pascal string too long")
    return bytes((len(text),)) + text


def _quicktime_meta_atom(
    asset_id: str,
    still_time: float,
    creation_dt: datetime,
    custom_exif: ExifMetadata | None = None,
) -> bytes:
    identifier = canonical_asset_id(asset_id)
    dt_to_use = (custom_exif.parsed_datetime() if custom_exif else None) or creation_dt
    if custom_exif and custom_exif.offset_time.strip():
        off = custom_exif.offset_time.strip().replace(":", "")
        if len(off) == 5 and off[0] in ("+", "-"):
            creation_iso = f"{dt_to_use.strftime('%Y-%m-%dT%H:%M:%S')}{off}"
        else:
            creation_iso = dt_to_use.astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    else:
        creation_iso = dt_to_use.astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")

    make_str = custom_exif.make.strip() if custom_exif else ""
    model_str = custom_exif.model.strip() if custom_exif else ""
    sw_str = custom_exif.software.strip() if custom_exif else ""

    entries: list[tuple[bytes, int, bytes]] = [
        (b"com.apple.quicktime.live-photo.auto", 22, b"\x01"),
        (b"com.apple.quicktime.live-photo.vitality-score", 23, struct.pack(">f", 0.95)),
        (b"com.apple.quicktime.live-photo.vitality-scoring-version", 21, struct.pack(">q", 4)),
        (b"com.apple.quicktime.creationdate", 1, creation_iso.encode("utf-8")),
        (CONTENT_KEY, 1, identifier.encode("ascii")),
        (STILL_TIME_KEY, 23, struct.pack(">f", float(max(0.0, still_time)))),
    ]
    if make_str:
        entries.append((b"com.apple.quicktime.make", 1, make_str.encode("utf-8")))
    if model_str:
        entries.append((b"com.apple.quicktime.model", 1, model_str.encode("utf-8")))
    if sw_str:
        entries.append((b"com.apple.quicktime.software", 1, sw_str.encode("utf-8")))
    if custom_exif and custom_exif.gps_lat is not None and custom_exif.gps_lon is not None:
        iso6709 = f"{custom_exif.gps_lat:+08.4f}{custom_exif.gps_lon:+09.4f}/".encode("ascii")
        entries.append((b"com.apple.quicktime.location.ISO6709", 1, iso6709))

    hdlr = atom(b"hdlr", _fullbox() + b"\x00" * 4 + b"mdta" + b"\x00" * 12 + b"\x00\x00")
    key_chunks = [struct.pack(">I4s", 8 + len(k), b"mdta") + k for k, _, _ in entries]
    keys = atom(b"keys", _fullbox() + struct.pack(">I", len(entries)) + b"".join(key_chunks))

    ilst_items: list[bytes] = []
    for idx, (_k, dtype, val) in enumerate(entries, start=1):
        data_box = atom(b"data", struct.pack(">II", dtype, 0) + val)
        ilst_items.append(atom(struct.pack(">I", idx), data_box))
    ilst = atom(b"ilst", b"".join(ilst_items))
    return atom(b"meta", hdlr + keys + ilst)


def _timed_metadata_track(
    track_id: int,
    video_track_id: int,
    timescale: int,
    cover_ticks: int,
    sample_offset: int,
    use_co64: bool,
) -> bytes:
    track_duration = cover_ticks + 1
    matrix = struct.pack(
        ">9I",
        0x00010000,
        0,
        0,
        0,
        0x00010000,
        0,
        0,
        0,
        0x40000000,
    )
    tkhd = atom(
        b"tkhd",
        _fullbox(flags=0x0F)
        + struct.pack(">IIIII", 0, 0, track_id, 0, track_duration)
        + b"\x00" * 8
        + struct.pack(">hhhh", 0, 0, 0, 0)
        + matrix
        + struct.pack(">II", 0, 0),
    )

    edits: list[bytes] = []
    if cover_ticks > 0:
        edits.append(struct.pack(">IiHH", cover_ticks, -1, 1, 0))
    edits.append(struct.pack(">IiHH", 1, 0, 1, 0))
    elst = atom(b"elst", _fullbox() + struct.pack(">I", len(edits)) + b"".join(edits))
    edts = atom(b"edts", elst)
    tref = atom(
        b"tref",
        atom(b"cdsc", struct.pack(">I", video_track_id))
        + atom(b"cdep", struct.pack(">I", video_track_id)),
    )

    mdhd = atom(
        b"mdhd",
        _fullbox()
        + struct.pack(">IIII", 0, 0, timescale, 1)
        + struct.pack(">HH", 0x55C4, 0),
    )
    hdlr = atom(
        b"hdlr",
        _fullbox()
        + b"mhlrmetaappl"
        + struct.pack(">II", 1, 0)
        + _pascal(b"Core Media Metadata"),
    )
    gmin = atom(
        b"gmin",
        _fullbox() + struct.pack(">H3HHH", 0x0040, 0x8000, 0x8000, 0x8000, 0, 0),
    )
    gmhd = atom(b"gmhd", gmin)
    data_hdlr = atom(
        b"hdlr",
        _fullbox()
        + b"dhlralisappl"
        + struct.pack(">II", 0, 0)
        + _pascal(b"Core Media Data Handler"),
    )
    alias = atom(b"alis", _fullbox(flags=1))
    dref = atom(b"dref", _fullbox() + struct.pack(">I", 1) + alias)
    dinf = atom(b"dinf", dref)

    keyd = atom(b"keyd", b"mdta" + STILL_TIME_KEY)
    dtyp = atom(b"dtyp", _fullbox() + struct.pack(">I", 0x41))
    key_record = struct.pack(">II", 8 + len(keyd) + len(dtyp), 1) + keyd + dtyp
    keys = atom(b"keys", key_record)
    mebx = atom(b"mebx", b"\x00" * 6 + struct.pack(">H", 1) + keys)
    stsd = atom(b"stsd", _fullbox() + struct.pack(">I", 1) + mebx)
    stts = atom(b"stts", _fullbox() + struct.pack(">III", 1, 1, 1))
    stsc = atom(b"stsc", _fullbox() + struct.pack(">IIII", 1, 1, 1, 1))
    stsz = atom(b"stsz", _fullbox() + struct.pack(">II", 0, 1) + struct.pack(">I", len(TIMED_SAMPLE)))
    if not use_co64:
        chunk_offsets = atom(b"stco", _fullbox() + struct.pack(">II", 1, sample_offset))
    else:
        chunk_offsets = atom(b"co64", _fullbox() + struct.pack(">IQ", 1, sample_offset))
    stbl = atom(b"stbl", stsd + stts + stsc + stsz + chunk_offsets)
    minf = atom(b"minf", gmhd + data_hdlr + dinf + stbl)
    mdia = atom(b"mdia", mdhd + hdlr + minf)
    return atom(b"trak", tkhd + edts + tref + mdia)


def _movie_timescale(data: bytes, moov: Atom) -> int:
    mvhd = first_child(data, moov, b"mvhd")
    if mvhd is None:
        raise ValueError("MOV 缺少 mvhd")
    version = data[mvhd.payload_start]
    position = mvhd.start + (28 if version == 1 else 20)
    timescale = int.from_bytes(data[position : position + 4], "big")
    if timescale <= 0:
        raise ValueError("MOV 时间基无效")
    return timescale


def _track_id(data: bytes, trak: Atom) -> int:
    tkhd = first_child(data, trak, b"tkhd")
    if tkhd is None:
        raise ValueError("MOV 轨道缺少 tkhd")
    version = data[tkhd.payload_start]
    position = tkhd.start + (28 if version == 1 else 20)
    return int.from_bytes(data[position : position + 4], "big")


def _track_handler(data: bytes, trak: Atom) -> bytes | None:
    mdia = first_child(data, trak, b"mdia")
    if mdia is None:
        return None
    hdlr = first_child(data, mdia, b"hdlr")
    if hdlr is None or hdlr.start + 20 > hdlr.end:
        return None
    return data[hdlr.start + 16 : hdlr.start + 20]


def _top_required(data: bytes) -> tuple[list[Atom], Atom, Atom, Atom]:
    top = parse_atoms(data)
    try:
        ftyp = next(item for item in top if item.type == b"ftyp")
        mdat = next(item for item in top if item.type == b"mdat")
        moov = next(item for item in top if item.type == b"moov")
    except StopIteration as exc:
        raise ValueError("MOV 必须包含 ftyp、mdat 和 moov") from exc
    return top, ftyp, mdat, moov


def write_live_mov(
    source: Path,
    output: Path,
    asset_id: str,
    still_time: float,
    duration: float,
    creation_dt: datetime | None = None,
    custom_exif: ExifMetadata | None = None,
) -> None:
    """Write authentic Apple Live Photo MOV (ftyp -> moov -> mdat) with full metadata & mebx track."""
    identifier = canonical_asset_id(asset_id)
    dt = creation_dt or datetime.now()
    if duration <= 0 or not 0 <= still_time <= duration:
        raise ValueError("Apple 封面时刻必须位于视频片段内")

    data = source.read_bytes()
    _top, _ftyp, mdat, moov = _top_required(data)
    timescale = _movie_timescale(data, moov)
    tracks = [item for item in child_atoms(data, moov) if item.type == b"trak"]
    ids = [_track_id(data, item) for item in tracks]
    video = next((item for item in tracks if _track_handler(data, item) == b"vide"), None)
    if video is None:
        raise ValueError("MOV 中没有可关联的视频轨")
    video_track_id = _track_id(data, video)
    metadata_track_id = max(ids, default=0) + 1
    cover_ticks = round(still_time * timescale)
    max_tick = max(0, round(duration * timescale) - 1)
    cover_ticks = min(max(0, cover_ticks), max_tick)

    ftyp_bytes = atom(b"ftyp", b"qt  \x00\x00\x00\x00qt  ")
    old_media = data[mdat.payload_start : mdat.end]
    new_mdat_payload = old_media + TIMED_SAMPLE
    new_mdat = atom(b"mdat", new_mdat_payload)
    mdat_hdr_len = 16 if new_mdat[:4] == b"\x00\x00\x00\x01" else 8

    raw_moov = moov.raw(data)
    parsed_raw_moov = parse_atoms(raw_moov)[0]
    kept_children_template = b"".join(
        child.raw(raw_moov)
        for child in child_atoms(raw_moov, parsed_raw_moov)
        if child.type != b"meta"
    )
    meta_bytes = _quicktime_meta_atom(identifier, still_time, dt, custom_exif=custom_exif)

    est_sample_offset = len(ftyp_bytes) + len(raw_moov) + len(meta_bytes) + 1024 + mdat_hdr_len + len(old_media)
    use_co64 = est_sample_offset > 0xFFFFFFFF

    pass1_meta_trak = _timed_metadata_track(
        metadata_track_id, video_track_id, timescale, cover_ticks, 0, use_co64
    )
    moov_size = len(atom(b"moov", kept_children_template + meta_bytes + pass1_meta_trak))

    new_media_start = len(ftyp_bytes) + moov_size + mdat_hdr_len
    delta = new_media_start - mdat.payload_start
    sample_offset = new_media_start + len(old_media)

    patched_moov = patch_chunk_offsets(raw_moov, delta)
    parsed_patched_moov = parse_atoms(patched_moov)[0]
    kept_children = b"".join(
        child.raw(patched_moov)
        for child in child_atoms(patched_moov, parsed_patched_moov)
        if child.type != b"meta"
    )
    final_meta_trak = _timed_metadata_track(
        metadata_track_id, video_track_id, timescale, cover_ticks, sample_offset, use_co64
    )
    new_moov = atom(b"moov", kept_children + meta_bytes + final_meta_trak)
    assert len(new_moov) == moov_size

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(ftyp_bytes + new_moov + new_mdat)


def _first_chunk_offset(data: bytes, trak: Atom) -> int:
    mdia = first_child(data, trak, b"mdia")
    minf = first_child(data, mdia, b"minf") if mdia else None
    stbl = first_child(data, minf, b"stbl") if minf else None
    if stbl is None:
        raise ValueError("MOV 轨道缺少 sample table")
    table = first_child(data, stbl, b"stco") or first_child(data, stbl, b"co64")
    if table is None:
        raise ValueError("MOV 轨道缺少 chunk offset")
    count = int.from_bytes(data[table.payload_start + 4 : table.payload_start + 8], "big")
    if count < 1:
        raise ValueError("MOV chunk offset 为空")
    width = 4 if table.type == b"stco" else 8
    start = table.payload_start + 8
    return int.from_bytes(data[start : start + width], "big")


def _cover_ticks(data: bytes, trak: Atom) -> int:
    edts = first_child(data, trak, b"edts")
    elst = first_child(data, edts, b"elst") if edts else None
    if elst is None:
        return 0
    version = data[elst.payload_start]
    count = int.from_bytes(data[elst.payload_start + 4 : elst.payload_start + 8], "big")
    position = elst.payload_start + 8
    if count >= 2:
        if version == 0:
            duration = int.from_bytes(data[position : position + 4], "big")
            media_time = int.from_bytes(data[position + 4 : position + 8], "big", signed=True)
        else:
            duration = int.from_bytes(data[position : position + 8], "big")
            media_time = int.from_bytes(data[position + 8 : position + 16], "big", signed=True)
        if media_time == -1:
            return duration
    return 0


def inspect_live_mov(path: Path) -> LiveMovInfo:
    data = path.read_bytes()
    _top, _ftyp, mdat, moov = _top_required(data)
    timescale = _movie_timescale(data, moov)
    moov_bytes = moov.raw(data)
    if CONTENT_KEY not in moov_bytes:
        raise ValueError("MOV 缺少 Apple content identifier 键")
    ids = re.findall(rb"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", moov_bytes)
    if not ids:
        raise ValueError("MOV 缺少 Apple 资产 UUID")
    identifier = canonical_asset_id(ids[-1].decode("ascii"))
    tracks = [item for item in child_atoms(data, moov) if item.type == b"trak"]
    video = next((item for item in tracks if _track_handler(data, item) == b"vide"), None)
    metadata = next(
        (
            item
            for item in tracks
            if _track_handler(data, item) == b"meta"
            and STILL_TIME_KEY in item.raw(data)
        ),
        None,
    )
    if video is None or metadata is None:
        raise ValueError("MOV 缺少视频轨或 still-image-time 元数据轨")

    video_offset = _first_chunk_offset(data, video)
    metadata_offset = _first_chunk_offset(data, metadata)
    if not (mdat.payload_start <= video_offset < mdat.end):
        raise ValueError("MOV 视频 chunk offset 超出 mdat 范围")
    if not (mdat.payload_start <= metadata_offset < mdat.end):
        raise ValueError("MOV metadata chunk offset 超出 mdat 范围")
    if data[metadata_offset : metadata_offset + 8] != TIMED_SAMPLE[:8]:
        raise ValueError("MOV still-image-time sample 结构无效")
    value = struct.unpack("b", data[metadata_offset + 8 : metadata_offset + 9])[0]
    return LiveMovInfo(
        asset_id=identifier,
        still_time=_cover_ticks(data, metadata) / timescale,
        video_track_id=_track_id(data, video),
        metadata_track_id=_track_id(data, metadata),
        video_chunk_offset=video_offset,
        metadata_chunk_offset=metadata_offset,
        sample_value=value,
    )


def verify_live_pair(img_path: Path, mov_path: Path) -> dict[str, object]:
    """Verify that HEIC/JPG and MOV share an identical ContentIdentifier and valid timed metadata."""
    img_uuid = inspect_live_image(img_path)
    mov_info = inspect_live_mov(mov_path)
    if img_uuid != mov_info.asset_id:
        raise ValueError(f"图片与 MOV UUID 不一致: {img_uuid} != {mov_info.asset_id}")
    return {
        "asset_id": img_uuid,
        "still_time": mov_info.still_time,
        "video_track_id": mov_info.video_track_id,
        "metadata_track_id": mov_info.metadata_track_id,
    }
