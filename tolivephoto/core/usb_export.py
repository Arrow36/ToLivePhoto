"""USB flash drive detection and Apple DCF (DCIM/100APPLE) export helpers."""
from __future__ import annotations

import ctypes
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psutil
from win32_setctime import setctime


@dataclass(frozen=True, slots=True)
class DriveInfo:
    root: Path
    label: str
    drive_type: str
    is_removable: bool
    free_gb: float

    @property
    def display_name(self) -> str:
        tag = "U盘" if self.is_removable else "外接磁盘"
        name = self.label or "无卷标"
        return f"[{tag}] {self.root} ({name}, 可用 {self.free_gb:.1f} GB)"


def list_usb_drives(include_system_drive: bool = False) -> list[DriveInfo]:
    """List removable USB drives first, followed by non-system fixed/external drives."""
    drives: list[DriveInfo] = []
    if os.name != "nt":
        return drives

    sys_drive = (os.environ.get("SystemDrive") or "C:").rstrip("\\/").upper()
    kernel32 = ctypes.windll.kernel32
    for part in psutil.disk_partitions(all=False):
        mount = part.mountpoint
        if not mount:
            continue
        if not include_system_drive and mount.rstrip("\\/").upper() == sys_drive:
            continue
        dtype = kernel32.GetDriveTypeW(ctypes.c_wchar_p(mount))
        if dtype not in (2, 3):
            continue
        is_removable = dtype == 2 or ("removable" in part.opts.lower())
        vol_buf = ctypes.create_unicode_buffer(256)
        kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(mount),
            vol_buf,
            256,
            None,
            None,
            None,
            None,
            0,
        )
        try:
            usage = shutil.disk_usage(mount)
            free_gb = usage.free / (1024**3)
        except OSError:
            free_gb = 0.0
        drives.append(
            DriveInfo(
                root=Path(mount),
                label=vol_buf.value,
                drive_type="REMOVABLE" if is_removable else "FIXED",
                is_removable=is_removable,
                free_gb=free_gb,
            )
        )

    drives.sort(key=lambda d: (not d.is_removable, str(d.root)))
    return drives


def normalize_dcf_prefix(prefix: str | None) -> str:
    """Normalize custom DCF prefix into a valid 4-character uppercase camera prefix (e.g. 'IMG_', 'CSI_')."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "", (prefix or "").strip().upper())
    if not cleaned:
        return "IMG_"
    if len(cleaned) >= 4:
        return cleaned[:4]
    return cleaned.ljust(4, "_")


def extract_4digit_seed(stem: str) -> int:
    """Extract a 4-digit DCF number from a filename stem like 'CSI_7607' -> 7607."""
    matches = re.findall(r"(\d{4})", stem)
    if matches:
        num = int(matches[-1])
        return max(1, min(9999, num))
    digits = re.findall(r"\d+", stem)
    if digits:
        num = int(digits[-1][-4:])
        if num > 0:
            return num
    return 1


def resolve_dcf_pair_paths(
    usb_root: Path,
    preferred_number: int | None = None,
    source_stem: str = "IMG_0001",
    image_ext: str = "HEIC",
    prefix: str = "IMG_",
) -> tuple[Path, Path, str, int]:
    """Resolve collision-free <usb_root>/DCIM/100APPLE/<PREFIX><XXXX>.{HEIC|JPG,MOV} paths."""
    dcim = Path(usb_root) / "DCIM"
    dcim.mkdir(parents=True, exist_ok=True)
    ext = image_ext.upper().lstrip(".")
    if ext not in ("HEIC", "JPG"):
        ext = "HEIC"
    pfx = normalize_dcf_prefix(prefix)

    num = preferred_number if preferred_number is not None else extract_4digit_seed(source_stem)
    num = max(1, min(9999, int(num)))

    folder_idx = 100
    while folder_idx <= 999:
        apple_dir = dcim / f"{folder_idx}APPLE"
        apple_dir.mkdir(parents=True, exist_ok=True)
        for candidate in range(num, 10000):
            stem = f"{pfx}{candidate:04d}"
            heic = apple_dir / f"{stem}.HEIC"
            jpg = apple_dir / f"{stem}.JPG"
            mov = apple_dir / f"{stem}.MOV"
            if not heic.exists() and not jpg.exists() and not mov.exists():
                return apple_dir / f"{stem}.{ext}", mov, stem, candidate
        num = 1
        folder_idx += 1

    raise RuntimeError("DCIM 目录编号已满 (100APPLE-999APPLE)")


def sync_pair_timestamps(img_path: Path, mov_path: Path, dt: datetime | None = None) -> None:
    """Synchronize creation and modification timestamps across image and MOV."""
    ts = (dt or datetime.now()).timestamp()
    for p in (img_path, mov_path):
        if os.name == "nt":
            try:
                setctime(str(p), ts)
            except OSError:
                pass
        os.utime(p, (ts, ts))
