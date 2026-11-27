"""Pure-Python EXIF metadata reader for RAW (.CR3/.CR2/.NEF/.ARW/.DNG/.RAF) and standard images."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from PIL import Image
from pillow_heif import register_heif_opener

register_heif_opener()

SIDECAR_EXIF_EXTENSIONS = (
    ".cr3",
    ".cr2",
    ".nef",
    ".arw",
    ".dng",
    ".raf",
    ".orf",
    ".rw2",
    ".pef",
    ".srw",
    ".heic",
    ".heif",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
)


@dataclass(slots=True)
class ExifMetadata:
    make: str = ""
    model: str = ""
    lens_model: str = ""
    software: str = ""
    datetime_original: str = ""  # YYYY:MM:DD HH:MM:SS
    offset_time: str = "+08:00"
    exposure_time: str = ""  # e.g. "1/250" or "0.5"
    f_number: str = ""  # e.g. "5.6"
    iso: int | None = None
    focal_length: str = ""  # e.g. "21.6"
    focal_length_35mm: int | None = None
    exposure_bias: str = ""  # e.g. "-1/3"
    artist: str = ""
    copyright: str = ""
    gps_lat: float | None = None
    gps_lon: float | None = None

    def is_empty(self) -> bool:
        return not any(
            [
                self.make.strip(),
                self.model.strip(),
                self.lens_model.strip(),
                self.software.strip(),
                self.datetime_original.strip(),
                self.exposure_time.strip(),
                self.f_number.strip(),
                self.iso is not None,
                self.focal_length.strip(),
                self.focal_length_35mm is not None,
                self.exposure_bias.strip(),
                self.artist.strip(),
                self.copyright.strip(),
                self.gps_lat is not None and self.gps_lon is not None,
            ]
        )

    def clone(self) -> ExifMetadata:
        return ExifMetadata(
            make=self.make,
            model=self.model,
            lens_model=self.lens_model,
            software=self.software,
            datetime_original=self.datetime_original,
            offset_time=self.offset_time,
            exposure_time=self.exposure_time,
            f_number=self.f_number,
            iso=self.iso,
            focal_length=self.focal_length,
            focal_length_35mm=self.focal_length_35mm,
            exposure_bias=self.exposure_bias,
            artist=self.artist,
            copyright=self.copyright,
            gps_lat=self.gps_lat,
            gps_lon=self.gps_lon,
        )

    def parsed_datetime(self) -> datetime | None:
        raw = self.datetime_original.strip()
        if not raw:
            return None
        for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                return datetime.strptime(raw[:19], fmt)
            except ValueError:
                continue
        return None

    def summary_text(self) -> str:
        if self.is_empty():
            return "默认 Apple 实况信息"
        parts: list[str] = []
        cam = self.model.strip() or self.make.strip()
        if cam:
            parts.append(cam)
        if self.focal_length.strip():
            parts.append(f"{self.focal_length.strip()}mm")
        if self.f_number.strip():
            parts.append(f"f/{self.f_number.strip()}")
        if self.exposure_time.strip():
            parts.append(f"{self.exposure_time.strip()}s")
        if self.iso is not None:
            parts.append(f"ISO{self.iso}")
        if not parts and self.datetime_original.strip():
            parts.append(self.datetime_original.strip())
        return " · ".join(parts) if parts else "已自定义 EXIF"


_TIFF_TYPE_SIZES = {
    1: 1,   # BYTE
    2: 1,   # ASCII
    3: 2,   # SHORT
    4: 4,   # LONG
    5: 8,   # RATIONAL
    6: 1,   # SBYTE
    7: 1,   # UNDEFINED
    8: 2,   # SSHORT
    9: 4,   # SLONG
    10: 8,  # SRATIONAL
    11: 4,  # FLOAT
    12: 8,  # DOUBLE
}


def _parse_tiff_ifd(data: bytes, offset: int, endian: str) -> dict[int, tuple[int, int, bytes]]:
    if offset < 0 or offset + 2 > len(data):
        return {}
    count = struct.unpack(endian + "H", data[offset : offset + 2])[0]
    entries: dict[int, tuple[int, int, bytes]] = {}
    for i in range(count):
        ep = offset + 2 + i * 12
        if ep + 12 > len(data):
            break
        tag, typ, cnt = struct.unpack(endian + "HHI", data[ep : ep + 8])
        unit = _TIFF_TYPE_SIZES.get(typ, 1)
        total = cnt * unit
        if total <= 4:
            raw = data[ep + 8 : ep + 8 + total]
        else:
            val_off = struct.unpack(endian + "I", data[ep + 8 : ep + 12])[0]
            if val_off + total > len(data):
                continue
            raw = data[val_off : val_off + total]
        entries[tag] = (typ, cnt, raw)
    return entries


def _decode_ascii(entry: tuple[int, int, bytes] | None) -> str:
    if not entry:
        return ""
    raw = entry[2].split(b"\x00", 1)[0]
    return raw.decode("utf-8", errors="replace").strip()


def _decode_uints(entry: tuple[int, int, bytes] | None, endian: str) -> list[int]:
    if not entry:
        return []
    typ, cnt, raw = entry
    if typ == 3 and len(raw) >= cnt * 2:
        return list(struct.unpack(endian + f"{cnt}H", raw[: cnt * 2]))
    if typ == 4 and len(raw) >= cnt * 4:
        return list(struct.unpack(endian + f"{cnt}I", raw[: cnt * 4]))
    if typ == 1 and len(raw) >= cnt:
        return list(raw[:cnt])
    return []


def _decode_rationals(entry: tuple[int, int, bytes] | None, endian: str, signed: bool = False) -> list[tuple[int, int]]:
    if not entry:
        return []
    _typ, cnt, raw = entry
    fmt = endian + ("ii" if signed else "II")
    out: list[tuple[int, int]] = []
    for i in range(cnt):
        pos = i * 8
        if pos + 8 > len(raw):
            break
        num, den = struct.unpack(fmt, raw[pos : pos + 8])
        out.append((num, den))
    return out


def _format_exposure_time(num: int, den: int) -> str:
    if den == 0 or num <= 0:
        return ""
    frac = Fraction(num, den)
    if frac >= 1:
        val = float(frac)
        return f"{val:g}"
    if frac.numerator == 1:
        return f"1/{frac.denominator}"
    inv = round(den / num)
    if inv > 1 and abs((1.0 / inv) - float(frac)) < 0.002 * float(frac):
        return f"1/{inv}"
    return f"{float(frac):g}"


def _format_decimal_rational(num: int, den: int, digits: int = 1) -> str:
    if den == 0:
        return ""
    val = num / den
    if abs(val - round(val)) < 1e-4:
        return str(int(round(val)))
    return f"{val:.{digits}f}".rstrip("0").rstrip(".")


def _format_exposure_bias(num: int, den: int) -> str:
    if den == 0:
        return ""
    if num == 0:
        return "0"
    frac = Fraction(num, den)
    if frac.denominator == 1:
        return f"{frac.numerator:+d}" if frac.numerator > 0 else str(frac.numerator)
    return f"{frac.numerator:+d}/{frac.denominator}" if frac.numerator > 0 else f"{frac.numerator}/{frac.denominator}"


def _decode_gps_coord(rats: list[tuple[int, int]], ref: str) -> float | None:
    if len(rats) < 3:
        return None
    vals: list[float] = []
    for n, d in rats[:3]:
        if d == 0:
            return None
        vals.append(n / d)
    deg = vals[0] + vals[1] / 60.0 + vals[2] / 3600.0
    if ref.upper() in ("S", "W"):
        deg = -deg
    return round(deg, 7)


def _apply_ifd_to_meta(
    meta: ExifMetadata,
    ifd0: dict[int, tuple[int, int, bytes]] | None,
    exif_ifd: dict[int, tuple[int, int, bytes]] | None,
    gps_ifd: dict[int, tuple[int, int, bytes]] | None,
    endian: str,
) -> None:
    if ifd0:
        meta.make = _decode_ascii(ifd0.get(0x010F)) or meta.make
        meta.model = _decode_ascii(ifd0.get(0x0110)) or meta.model
        meta.software = _decode_ascii(ifd0.get(0x0131)) or meta.software
        meta.artist = _decode_ascii(ifd0.get(0x013B)) or meta.artist
        meta.copyright = _decode_ascii(ifd0.get(0x8298)) or meta.copyright
        if not meta.datetime_original:
            meta.datetime_original = _decode_ascii(ifd0.get(0x0132))

    if exif_ifd:
        dt_orig = _decode_ascii(exif_ifd.get(0x9003))
        if dt_orig:
            meta.datetime_original = dt_orig
        off_orig = _decode_ascii(exif_ifd.get(0x9011)) or _decode_ascii(exif_ifd.get(0x9010))
        if off_orig:
            meta.offset_time = off_orig

        exp = _decode_rationals(exif_ifd.get(0x829A), endian)
        if exp:
            meta.exposure_time = _format_exposure_time(*exp[0]) or meta.exposure_time

        fnum = _decode_rationals(exif_ifd.get(0x829D), endian)
        if fnum:
            meta.f_number = _format_decimal_rational(*fnum[0], digits=2) or meta.f_number

        iso_vals = _decode_uints(exif_ifd.get(0x8827), endian)
        if iso_vals:
            meta.iso = int(iso_vals[0])

        fl = _decode_rationals(exif_ifd.get(0x920A), endian)
        if fl:
            meta.focal_length = _format_decimal_rational(*fl[0], digits=1) or meta.focal_length

        fl35 = _decode_uints(exif_ifd.get(0xA405), endian)
        if fl35 and fl35[0] > 0:
            meta.focal_length_35mm = int(fl35[0])

        ev = _decode_rationals(exif_ifd.get(0x9204), endian, signed=True)
        if ev:
            meta.exposure_bias = _format_exposure_bias(*ev[0]) or meta.exposure_bias

        lens = _decode_ascii(exif_ifd.get(0xA434))
        if lens:
            meta.lens_model = lens
        elif not meta.lens_model and 0xA432 in exif_ifd:
            spec = _decode_rationals(exif_ifd.get(0xA432), endian)
            if len(spec) >= 4 and spec[0][1] > 0 and spec[1][1] > 0:
                fmin = _format_decimal_rational(*spec[0], digits=1)
                fmax = _format_decimal_rational(*spec[1], digits=1)
                if fmin and fmax:
                    meta.lens_model = f"{fmin}mm" if fmin == fmax else f"{fmin}-{fmax}mm"

    if gps_ifd:
        lat_ref = _decode_ascii(gps_ifd.get(0x0001))
        lat_rats = _decode_rationals(gps_ifd.get(0x0002), endian)
        lon_ref = _decode_ascii(gps_ifd.get(0x0003))
        lon_rats = _decode_rationals(gps_ifd.get(0x0004), endian)
        lat = _decode_gps_coord(lat_rats, lat_ref or "N")
        lon = _decode_gps_coord(lon_rats, lon_ref or "E")
        if lat is not None and lon is not None:
            meta.gps_lat = lat
            meta.gps_lon = lon


def _parse_tiff_blob(tiff: bytes, meta: ExifMetadata | None = None, as_direct_ifd_role: str | None = None) -> ExifMetadata:
    if meta is None:
        meta = ExifMetadata()
    if tiff.startswith(b"Exif\x00\x00"):
        tiff = tiff[6:]
    if len(tiff) < 8:
        return meta
    if tiff[:4] == b"MM\x00*":
        endian = ">"
    elif tiff[:4] in (b"II*\x00", b"IIU\x00"):
        endian = "<"
    else:
        return meta

    first_ifd_off = struct.unpack(endian + "I", tiff[4:8])[0]
    first_ifd = _parse_tiff_ifd(tiff, first_ifd_off, endian)

    if as_direct_ifd_role == "exif":
        _apply_ifd_to_meta(meta, None, first_ifd, None, endian)
        return meta
    if as_direct_ifd_role == "gps":
        _apply_ifd_to_meta(meta, None, None, first_ifd, endian)
        return meta
    if as_direct_ifd_role == "canon_maker":
        lens = _decode_ascii(first_ifd.get(0x0095))
        if lens and not meta.lens_model:
            meta.lens_model = lens
        return meta

    exif_ifd = None
    gps_ifd = None
    if 0x8769 in first_ifd:
        sub_off = _decode_uints(first_ifd.get(0x8769), endian)
        if sub_off:
            exif_ifd = _parse_tiff_ifd(tiff, sub_off[0], endian)
    if 0x8825 in first_ifd:
        gps_off = _decode_uints(first_ifd.get(0x8825), endian)
        if gps_off:
            gps_ifd = _parse_tiff_ifd(tiff, gps_off[0], endian)

    _apply_ifd_to_meta(meta, first_ifd, exif_ifd, gps_ifd, endian)
    return meta


def _read_cr3_exif(path: Path) -> ExifMetadata:
    meta = ExifMetadata()
    with path.open("rb") as fh:
        head = fh.read(1024 * 1024)

    for tag, role in ((b"CMT1", None), (b"CMT2", "exif"), (b"CMT3", "canon_maker"), (b"CMT4", "gps")):
        idx = head.find(tag)
        while idx != -1:
            if idx + 12 <= len(head) and head[idx + 4 : idx + 8] in (b"II*\x00", b"MM\x00*"):
                box_size = struct.unpack(">I", head[idx - 4 : idx])[0] if idx >= 4 else 0
                end = min(len(head), idx - 4 + box_size) if box_size >= 16 else len(head)
                _parse_tiff_blob(head[idx + 4 : end], meta=meta, as_direct_ifd_role=role)
                break
            idx = head.find(tag, idx + 4)
    return meta


def _extract_jpeg_app1_exif(jpeg_bytes: bytes) -> bytes | None:
    if not jpeg_bytes.startswith(b"\xff\xd8"):
        return None
    pos = 2
    while pos + 4 <= len(jpeg_bytes):
        if jpeg_bytes[pos] != 0xFF:
            break
        marker = jpeg_bytes[pos + 1]
        if marker in (0xD8, 0xD9):
            pos += 2
            continue
        if pos + 4 > len(jpeg_bytes):
            break
        seg_len = struct.unpack(">H", jpeg_bytes[pos + 2 : pos + 4])[0]
        if marker == 0xE1 and jpeg_bytes[pos + 4 : pos + 10] == b"Exif\x00\x00":
            return jpeg_bytes[pos + 4 : pos + 2 + seg_len]
        pos += 2 + seg_len
    return None


def read_exif_from_file(path: Path) -> ExifMetadata:
    """Extract camera/shooting EXIF from .CR3/.CR2/.NEF/.ARW/.DNG/.RAF/.JPG/.HEIC/.TIFF/.PNG."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到图片或 RAW 文件: {path}")

    ext = path.suffix.lower()
    if ext == ".cr3":
        return _read_cr3_exif(path)

    if ext == ".raf":
        with path.open("rb") as fh:
            hdr = fh.read(128)
            if hdr.startswith(b"FUJIFILMCCD-RAW") and len(hdr) >= 92:
                jpg_off, jpg_len = struct.unpack(">II", hdr[84:92])
                if jpg_len > 32:
                    fh.seek(jpg_off)
                    jpg_data = fh.read(min(jpg_len, 256 * 1024))
                    app1 = _extract_jpeg_app1_exif(jpg_data)
                    if app1:
                        return _parse_tiff_blob(app1)
        return ExifMetadata()

    if ext in (".cr2", ".nef", ".arw", ".dng", ".orf", ".rw2", ".pef", ".srw", ".tif", ".tiff"):
        with path.open("rb") as fh:
            head = fh.read(4 * 1024 * 1024)
        return _parse_tiff_blob(head)

    with Image.open(path) as img:
        raw_exif = img.info.get("exif")
        if isinstance(raw_exif, (bytes, bytearray)) and raw_exif:
            return _parse_tiff_blob(bytes(raw_exif))
    return ExifMetadata()


def _find_in_dir(directory: Path, stem_lower: str) -> Path | None:
    if not directory.is_dir():
        return None
    by_ext: dict[str, Path] = {}
    for entry in directory.iterdir():
        if entry.is_file() and entry.stem.lower() == stem_lower:
            ext = entry.suffix.lower()
            if ext in SIDECAR_EXIF_EXTENSIONS:
                by_ext[ext] = entry
    for ext in SIDECAR_EXIF_EXTENSIONS:
        if ext in by_ext:
            return by_ext[ext]
    return None


def find_matching_sidecar_exif_file(video_path: Path, search_dir: Path | None = None) -> Path | None:
    """Locate a same-stem RAW or photo file beside video_path or inside an explicit search_dir."""
    base_dir = Path(search_dir) if search_dir is not None else video_path.parent
    stem_lower = video_path.stem.lower()
    if not base_dir.is_dir():
        return None

    direct = _find_in_dir(base_dir, stem_lower)
    if direct is not None:
        return direct

    if search_dir is not None:
        for sub in sorted(base_dir.iterdir()):
            if sub.is_dir():
                hit = _find_in_dir(sub, stem_lower)
                if hit is not None:
                    return hit
    return None
