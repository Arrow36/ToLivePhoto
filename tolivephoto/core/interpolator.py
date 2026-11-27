"""RIFE (rife-ncnn-vulkan) optical flow frame interpolation wrapper with VRAM-safe threads, black-frame guard & instant cancel."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

import cv2

from .tools import ConversionCancelled, _creation_flags, find_rife, kill_process_tree

ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]


def _choose_rife_threads(model_name: str, width: int, height: int, gpu_id: int) -> str:
    """Pick VRAM-safe -j load:proc:save threads to prevent OOM black frames on high-res inputs."""
    if gpu_id < 0:
        return "2:2:2"
    pixels = max(1, int(width) * int(height))
    if "4.25" in model_name or pixels >= 3_600_000:
        return "2:1:4"
    return "2:2:4"


def _has_vram_or_black_frame_issue(
    input_dir: Path,
    produced: list[Path],
    ext: str,
    log_text: str,
) -> bool:
    low_log = log_text.lower()
    if "vkallocatememory failed" in low_log or "vkqueuesubmit failed" in low_log:
        return True
    if len(produced) < 3:
        return False

    in_frames = sorted(input_dir.glob(f"*.{ext}"))
    if not in_frames:
        return False
    in0 = cv2.imread(str(in_frames[0]))
    if in0 is None or float(in0.mean()) < 4.0:
        return False

    sample_indices = sorted(
        {
            1,
            min(7, len(produced) - 1),
            min((len(produced) // 2) | 1, len(produced) - 1),
            max(0, len(produced) - 2),
        }
    )
    for idx in sample_indices:
        img = cv2.imread(str(produced[idx]))
        if img is not None and float(img.mean()) < 1.0:
            return True
    return False


def _run_rife_pass(
    rife_exe: Path,
    rife_dir: Path,
    input_dir: Path,
    output_dir: Path,
    target_frames: int,
    model_name: str,
    gpu_id: int,
    threads: str,
    uhd: bool,
    ext: str,
    progress_cb: ProgressCallback | None,
    cancel_cb: CancelCallback | None,
) -> tuple[int, str, list[Path]]:
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
        "-g",
        str(gpu_id),
        "-j",
        threads,
        "-f",
        f"%08d.{ext}",
    ]
    if uhd:
        cmd.append("-u")

    glob_pat = f"*.{ext}"
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as log_f:
        proc = subprocess.Popen(
            cmd,
            cwd=str(rife_dir),
            stdout=log_f,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_creation_flags(),
        )

        last_count = -1
        while proc.poll() is None:
            if cancel_cb and cancel_cb():
                kill_process_tree(proc)
                raise ConversionCancelled("用户已取消转换")
            if progress_cb is not None:
                done_count = sum(1 for _ in output_dir.glob(glob_pat))
                if done_count != last_count:
                    last_count = done_count
                    pct = 25 + int(45 * min(1.0, done_count / max(1, target_frames)))
                    accel_tag = f"GPU:{gpu_id}" if gpu_id >= 0 else "CPU"
                    progress_cb(pct, f"AI 光流补帧中 ({model_name} · {accel_tag}): {done_count} / {target_frames} 帧")
            time.sleep(0.08)

        log_f.seek(0)
        log_text = log_f.read()
        produced = sorted(output_dir.glob(glob_pat))
        return proc.returncode, log_text, produced


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
    """Run rife-ncnn-vulkan on image sequence with VRAM-safe threads, black-frame guard & instant cancel."""
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
    threads = _choose_rife_threads(model_name, width, height, gpu_id)
    uhd = max(width, height) >= 3840 or ("4.25" in model_name and max(width, height) >= 3200)

    rc, log_text, produced = _run_rife_pass(
        rife_exe=rife_exe,
        rife_dir=rife_dir,
        input_dir=input_dir,
        output_dir=output_dir,
        target_frames=target_frames,
        model_name=model_name,
        gpu_id=gpu_id,
        threads=threads,
        uhd=uhd,
        ext=ext,
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
    )

    if rc != 0 or _has_vram_or_black_frame_issue(input_dir, produced, ext, log_text):
        if cancel_cb and cancel_cb():
            raise ConversionCancelled("用户已取消转换")
        if progress_cb is not None:
            progress_cb(25, "检测到显存压力，正以低显存安全模式重试补帧...")
        shutil.rmtree(output_dir, ignore_errors=True)
        rc, log_text, produced = _run_rife_pass(
            rife_exe=rife_exe,
            rife_dir=rife_dir,
            input_dir=input_dir,
            output_dir=output_dir,
            target_frames=target_frames,
            model_name=model_name,
            gpu_id=gpu_id,
            threads="1:1:2",
            uhd=True,
            ext=ext,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )
        if rc != 0:
            raise RuntimeError(f"RIFE 补帧失败 (exit={rc}):\n{log_text}")
        if _has_vram_or_black_frame_issue(input_dir, produced, ext, log_text):
            raise RuntimeError("GPU 显存不足导致补帧异常，请切换 rife-v4.6 模型或降低视频长边分辨率。")

    if not produced:
        raise RuntimeError("RIFE 未生成任何输出帧。")
