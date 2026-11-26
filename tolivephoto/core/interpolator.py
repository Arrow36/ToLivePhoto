"""RIFE (rife-ncnn-vulkan) optical flow frame interpolation wrapper with cancellation."""
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
    progress_cb: ProgressCallback | None = None,
    cancel_cb: CancelCallback | None = None,
) -> None:
    """Run rife-ncnn-vulkan on PNG sequence with live progress polling and cancellation."""
    rife_exe = find_rife()
    if rife_exe is None:
        raise FileNotFoundError("未找到 rife-ncnn-vulkan.exe，请先点击「一键下载 RIFE」或运行 scripts/setup_rife.py。")

    rife_dir = rife_exe.parent
    model_dir = rife_dir / model_name
    if not (model_dir / "flownet.bin").is_file():
        for fallback in ("rife-v4.25", "rife-v4.6", "rife-v4.25-lite"):
            if (rife_dir / fallback / "flownet.bin").is_file():
                model_dir = rife_dir / fallback
                model_name = fallback
                break
        else:
            raise FileNotFoundError(f"未找到 RIFE 模型目录: {model_name}")

    output_dir.mkdir(parents=True, exist_ok=True)
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
        "-f",
        "%08d.png",
    ]
    if max(width, height) >= 2160:
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
    while proc.poll() is None:
        if cancel_cb and cancel_cb():
            proc.terminate()
            proc.wait()
            raise ConversionCancelled("用户已取消转换")
        if progress_cb is not None:
            done_count = sum(1 for _ in output_dir.glob("*.png"))
            if done_count != last_count:
                last_count = done_count
                pct = 25 + int(45 * min(1.0, done_count / max(1, target_frames)))
                progress_cb(pct, f"AI 光流补帧中 ({model_name}): {done_count} / {target_frames} 帧")
        time.sleep(0.15)

    stdout, stderr = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"RIFE 补帧失败 (exit={proc.returncode}):\n{stderr or stdout}")

    produced = sorted(output_dir.glob("*.png"))
    if not produced:
        raise RuntimeError("RIFE 未生成任何输出帧。")
