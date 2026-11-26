"""External binary discovery, GPU capability checks, and windowless subprocess runner."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from functools import cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BIN_DIR = PROJECT_ROOT / "bin"


def _creation_flags() -> int:
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0


def _find_winget_ffmpeg(exe_name: str) -> Path | None:
    local_app = os.environ.get("LOCALAPPDATA")
    if not local_app:
        return None
    winget_dir = Path(local_app) / "Microsoft" / "WinGet" / "Packages"
    if not winget_dir.exists():
        return None
    for pkg in winget_dir.glob("Gyan.FFmpeg*"):
        for match in pkg.rglob(exe_name):
            if match.is_file():
                return match
    return None


def find_ffmpeg() -> Path:
    local_bin = BIN_DIR / "ffmpeg" / "ffmpeg.exe"
    if local_bin.is_file():
        return local_bin
    on_path = shutil.which("ffmpeg")
    if on_path:
        return Path(on_path)
    winget = _find_winget_ffmpeg("ffmpeg.exe")
    if winget:
        return winget
    raise FileNotFoundError("未找到 ffmpeg.exe，请确保已安装 FFmpeg 或将其放入 bin/ffmpeg/ 目录。")


def find_ffprobe() -> Path:
    local_bin = BIN_DIR / "ffmpeg" / "ffprobe.exe"
    if local_bin.is_file():
        return local_bin
    on_path = shutil.which("ffprobe")
    if on_path:
        return Path(on_path)
    winget = _find_winget_ffmpeg("ffprobe.exe")
    if winget:
        return winget
    raise FileNotFoundError("未找到 ffprobe.exe，请确保已安装 FFmpeg 或将其放入 bin/ffmpeg/ 目录。")


def find_rife() -> Path | None:
    candidate = BIN_DIR / "rife" / "rife-ncnn-vulkan.exe"
    if candidate.is_file():
        return candidate
    on_path = shutil.which("rife-ncnn-vulkan")
    if on_path:
        return Path(on_path)
    return None


def list_available_rife_models() -> list[str]:
    rife_exe = find_rife()
    if rife_exe is None:
        return []
    rife_dir = rife_exe.parent
    models: list[str] = []
    for preferred in ("rife-v4.25", "rife-v4.6", "rife-v4.25-lite"):
        if (rife_dir / preferred / "flownet.bin").is_file():
            models.append(preferred)
    for child in sorted(rife_dir.iterdir()):
        if child.is_dir() and child.name not in models and (child / "flownet.bin").is_file():
            models.append(child.name)
    return models


@cache
def list_vulkan_gpus() -> list[tuple[int, str]]:
    """Return available Vulkan GPU devices reported by rife-ncnn-vulkan -l."""
    rife_exe = find_rife()
    if rife_exe is None:
        return []
    proc = subprocess.run(
        [str(rife_exe), "-l"],
        cwd=str(rife_exe.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_creation_flags(),
    )
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    gpus: list[tuple[int, str]] = []
    seen: set[int] = set()
    for match in re.finditer(r"\[(\d+)\s+([^\]]+)\]", text):
        gid = int(match.group(1))
        name = match.group(2).strip()
        if gid not in seen:
            seen.add(gid)
            gpus.append((gid, name))
    return gpus


@cache
def has_nvenc() -> bool:
    """Detect whether h264_nvenc works on this machine."""
    try:
        ffmpeg = find_ffmpeg()
    except FileNotFoundError:
        return False
    proc = subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=256x256:r=30:d=0.1",
            "-c:v",
            "h264_nvenc",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_creation_flags(),
    )
    return proc.returncode == 0


def run_command(
    cmd: list[str],
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_creation_flags(),
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"命令执行失败 (exit={proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}"
        )
    return proc
