"""RIFE (rife-ncnn-vulkan) optical flow frame interpolation wrapper with fast JPG I/O & GPU acceleration."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Callable

from .tools import _creation_flags, find_rife

ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]


class ConversionCancelled(RuntimeError):
    pass


def interpolate_frames_rife(
    input_dir: Path,
    output_dir: Path,
    target_frames: int,
    model_name: str,
    width: int,
    height: int,
    gpu_id: int = 0,
    frame_ext: str = "jpg",
    progress_cb: ProgressCallback | None = None,
    cancel_cb: CancelCallback | None = None,
) -> None:
    """Run rife-ncnn-vulkan on image sequence with high-throughput GPU threads & cancellation."""
    rife_exe = find_rife()
    if rife_exe is None:
        raise FileNotFoundError("未找到 rife-ncnn-vulkan.exe，请先点击「一键下载 RIFE」或运行 scripts/setup_rife.py。")

    rife_dir = rife_exe.parent
    model_dir = rife_dir / model_name
    if not (model_dir / "flownet.bin").is_file():
        for fallback in ("rife-v4.6", "rife-v4.25", "rife-v4.25-lite"):
            if (rife_dir / fallback / "flownet.bin").is_file():
                model_dir = rife_dir / fallback
                model_name = fallback
                break
        else:
            raise FileNotFoundError(f"未找到 RIFE 模型目录: {model_name}")

    ext = frame_ext.lower().lstrip(".") or "jpg"
    output_dir.mkdir(parents=True, exist_ok=True)
    threads = "4:4:6" if gpu_id >= 0 else "2:2:2"
    cmd = [
        str(rife_exe),
        "-i",
        str(input_dir),
        "-o",
        str(output_dir),
        "-n",
        str(target_frames),
        "-m",
        model_name,
        "-g",
        str(gpu_id),
        "-j",
        threads,
        "-f",
        f"%08d.{ext}",
    ]
    if max(width, height) >= 3840:
        cmd.append("-u")

    proc = subprocess.Popen(
        cmd,
        cwd=str(rife_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_creation_flags(),
    )

    last_count = -1
    glob_pat = f"*.{ext}"
    while proc.poll() is None:
        if cancel_cb and cancel_cb():
            proc.terminate()
            proc.wait()
            raise ConversionCancelled("用户已取消转换")
        if progress_cb is not None:
            done_count = sum(1 for _ in output_dir.glob(glob_pat))
            if done_count != last_count:
                last_count = done_count
                pct = 25 + int(45 * min(1.0, done_count / max(1, target_frames)))
                accel_tag = f"GPU:{gpu_id}" if gpu_id >= 0 else "CPU"
                progress_cb(pct, f"AI 光流补帧中 ({model_name} · {accel_tag}): {done_count} / {target_frames} 帧")
        time.sleep(0.12)

    stdout, stderr = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"RIFE 补帧失败 (exit={proc.returncode}):\n{stderr or stdout}")

    produced = sorted(output_dir.glob(glob_pat))
    if not produced:
        raise RuntimeError("RIFE 未生成任何输出帧。")
