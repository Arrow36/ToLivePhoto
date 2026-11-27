"""Selective HTTP Range downloader for rife-ncnn-vulkan Windows binaries and models."""
from __future__ import annotations

import shutil
import struct
import urllib.request
import zlib
from pathlib import Path

from .tools import APP_DIR

BIN_RIFE_DIR = APP_DIR / "bin" / "rife"

TNTWISE_URL = "https://github.com/TNTwise/rife-ncnn-vulkan/releases/download/20250112/windows.zip"
NIHUI_URL = "https://github.com/nihui/rife-ncnn-vulkan/releases/download/20221029/rife-ncnn-vulkan-20221029-windows.zip"

WANTED_SUFFIXES = (
    "rife-ncnn-vulkan.exe",
    "vcomp140.dll",
    "rife-v4.25/flownet.bin",
    "rife-v4.25/flownet.param",
    "rife-v4.6/flownet.bin",
    "rife-v4.6/flownet.param",
)


def _http_range_get(url: str, start: int, end: int, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}", "User-Agent": "ToLivePhoto-setup"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _resolve_zip_info(url: str) -> tuple[str, int]:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "ToLivePhoto-setup"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        total = int(resp.headers["Content-Length"])
        accept_ranges = resp.headers.get("Accept-Ranges", "")
        if "bytes" not in accept_ranges.lower():
            raise RuntimeError(f"Server does not advertise byte-range support: {accept_ranges!r}")
        return resp.url, total


def _parse_central_directory(final_url: str, total: int) -> list[tuple[str, int, int, int, int]]:
    tail_size = min(131072, total)
    tail = _http_range_get(final_url, total - tail_size, total - 1)
    eocd_pos = tail.rfind(b"PK\x05\x06")
    if eocd_pos < 0:
        raise RuntimeError("Could not locate ZIP End-of-Central-Directory record")
    _, _, _, _, _entries, cd_size, cd_offset, _ = struct.unpack("<4sHHHHIIH", tail[eocd_pos : eocd_pos + 22])
    cd = _http_range_get(final_url, cd_offset, cd_offset + cd_size - 1)

    results: list[tuple[str, int, int, int, int]] = []
    pos = 0
    while pos < len(cd) and cd[pos : pos + 4] == b"PK\x01\x02":
        fields = struct.unpack("<4sHHHHHHIIIHHHHHII", cd[pos : pos + 46])
        method = fields[4]
        comp_size = fields[8]
        uncomp_size = fields[9]
        fname_len = fields[10]
        extra_len = fields[11]
        comment_len = fields[12]
        local_hdr_off = fields[16]
        fname = cd[pos + 46 : pos + 46 + fname_len].decode("utf-8", errors="replace")
        results.append((fname, method, comp_size, uncomp_size, local_hdr_off))
        pos += 46 + fname_len + extra_len + comment_len
    return results


def _download_zip_entry(final_url: str, method: int, comp_size: int, local_hdr_off: int) -> bytes:
    hdr = _http_range_get(final_url, local_hdr_off, local_hdr_off + 29)
    if hdr[:4] != b"PK\x03\x04":
        raise RuntimeError(f"Invalid local file header at offset {local_hdr_off}")
    _, _, _, _, _, _, _, _, _, fname_len, extra_len = struct.unpack("<4sHHHHHIIIHH", hdr)
    data_start = local_hdr_off + 30 + fname_len + extra_len
    raw = _http_range_get(final_url, data_start, data_start + comp_size - 1, timeout=120)
    if method == 0:
        return raw
    if method == 8:
        return zlib.decompress(raw, -zlib.MAX_WBITS)
    raise RuntimeError(f"Unsupported ZIP compression method: {method}")


def _map_relative_dest(entry_name: str, target_dir: Path) -> Path | None:
    norm = entry_name.replace("\\", "/")
    for suffix in WANTED_SUFFIXES:
        if norm.endswith("/" + suffix) or norm == suffix:
            return target_dir / suffix
    return None


def _install_from_url(url: str, target_dir: Path) -> list[Path]:
    final_url, total = _resolve_zip_info(url)
    entries = _parse_central_directory(final_url, total)
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for fname, method, comp_size, _uncomp_size, local_hdr_off in entries:
        dest = _map_relative_dest(fname, target_dir)
        if dest is None or comp_size == 0:
            continue
        print(f"Downloading {fname} ({comp_size / 1024 / 1024:.2f} MB) -> {dest}")
        data = _download_zip_entry(final_url, method, comp_size, local_hdr_off)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        written.append(dest)
    return written


def install_rife(target_dir: Path | None = None) -> int:
    dest_dir = Path(target_dir) if target_dir is not None else BIN_RIFE_DIR
    exe_path = dest_dir / "rife-ncnn-vulkan.exe"
    v425_bin = dest_dir / "rife-v4.25" / "flownet.bin"
    v46_bin = dest_dir / "rife-v4.6" / "flownet.bin"
    if exe_path.exists() and (v425_bin.exists() or v46_bin.exists()):
        print(f"RIFE already present in {dest_dir}")
        return 0

    try:
        written = _install_from_url(TNTWISE_URL, dest_dir)
        if not any(p.name == "rife-ncnn-vulkan.exe" for p in written):
            raise RuntimeError("Missing rife-ncnn-vulkan.exe from TNTwise archive")
        print(f"Installed {len(written)} files from TNTwise release.")
        return 0
    except Exception as exc:
        print(f"TNTwise download failed ({exc}), falling back to nihui release...")
        if dest_dir.exists():
            shutil.rmtree(dest_dir, ignore_errors=True)
        written = _install_from_url(NIHUI_URL, dest_dir)
        if not any(p.name == "rife-ncnn-vulkan.exe" for p in written):
            raise RuntimeError("Missing rife-ncnn-vulkan.exe from nihui archive")
        print(f"Installed {len(written)} files from nihui fallback release.")
        return 0
