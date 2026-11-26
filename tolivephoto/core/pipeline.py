"""End-to-end video-to-iPhone Live Photo conversion pipeline with Polaroid padding, bitrate control & EXIF."""
from __future__ import annotations

import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import Image, ImageOps

from .apple_live import verify_live_pair, write_live_image, write_live_mov
from .interpolator import ConversionCancelled, interpolate_frames_rife
from .models import (
    ConversionJob,
    CropRect,
    FramePadding,
    TimelineSummary,
    VideoInfo,
    compute_timeline_summary,
)
from .probe import probe_video
from .tools import find_ffmpeg, has_nvenc, run_command
from .usb_export import resolve_dcf_pair_paths, sync_pair_timestamps

ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class ConversionResult:
    jpg_path: Path
    mov_path: Path
    livp_path: Path | None
    summary: TimelineSummary
    asset_id: str
    still_time: float
    used_dcf_number: int | None = None


def _parse_hex_rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.strip().lstrip("#")
    if len(s) != 6:
        return (255, 255, 255)
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return (255, 255, 255)


def compute_scaled_video_dims(w: int, h: int, max_long_edge: int) -> tuple[int, int]:
    if max_long_edge <= 0 or max(w, h) <= max_long_edge:
        return max(2, (w // 2) * 2), max(2, (h // 2) * 2)
    scale = max_long_edge / float(max(w, h))
    nw = max(2, int(round((w * scale) / 2.0) * 2))
    nh = max(2, int(round((h * scale) / 2.0) * 2))
    return nw, nh


def _make_blurred_background(rgb: np.ndarray, target_w: int, target_h: int, bg_blur_sigma: int) -> np.ndarray:
    vh, vw = rgb.shape[:2]
    scale = max(target_w / float(vw), target_h / float(vh))
    sw = max(target_w, int(round(vw * scale)))
    sh = max(target_h, int(round(vh * scale)))
    resized = cv2.resize(rgb, (sw, sh), interpolation=cv2.INTER_LINEAR)
    x0 = max(0, (sw - target_w) // 2)
    y0 = max(0, (sh - target_h) // 2)
    bg_crop = resized[y0 : y0 + target_h, x0 : x0 + target_w]
    dw = max(2, target_w // 4)
    dh = max(2, target_h // 4)
    small = cv2.resize(bg_crop, (dw, dh), interpolation=cv2.INTER_AREA)
    sigma = max(1.5, float(bg_blur_sigma) / 4.0)
    blurred = cv2.GaussianBlur(small, (0, 0), sigmaX=sigma, sigmaY=sigma)
    return cv2.resize(blurred, (target_w, target_h), interpolation=cv2.INTER_LINEAR)


def compose_cropped_frame_rgb(
    rgb: np.ndarray,
    crop: CropRect,
    bg_mode: str = "blur",
    bg_color: str = "#FFFFFF",
    bg_blur_sigma: int = 35,
    padding: FramePadding | None = None,
) -> np.ndarray:
    """Render cropped, out-of-bounds padded, or Polaroid-framed RGB frame."""
    vh, vw = rgb.shape[:2]
    c = crop.normalized(vw, vh, allow_out_of_bounds=True)
    pad = (padding or FramePadding()).normalized()

    if not c.is_out_of_bounds(vw, vh) and pad.is_zero():
        return rgb[c.y : c.y + c.h, c.x : c.x + c.w].copy()

    total_w = c.w + pad.left + pad.right
    total_h = c.h + pad.top + pad.bottom

    if bg_mode == "solid":
        r, g, b = _parse_hex_rgb(bg_color)
        canvas = np.empty((total_h, total_w, 3), dtype=np.uint8)
        canvas[:, :] = (r, g, b)
    else:
        canvas = _make_blurred_background(rgb, total_w, total_h, bg_blur_sigma)

    ix1 = max(0, c.x)
    iy1 = max(0, c.y)
    ix2 = min(vw, c.x + c.w)
    iy2 = min(vh, c.y + c.h)
    if ix2 > ix1 and iy2 > iy1:
        dx = pad.left + (ix1 - c.x)
        dy = pad.top + (iy1 - c.y)
        canvas[dy : dy + (iy2 - iy1), dx : dx + (ix2 - ix1)] = rgb[iy1:iy2, ix1:ix2]
    return canvas


def _build_atempo_chain(speed_ratio: float) -> str:
    ratio = max(0.1, min(10.0, float(speed_ratio)))
    parts: list[str] = []
    while ratio > 2.0:
        parts.append("atempo=2.0")
        ratio /= 2.0
    while ratio < 0.5:
        parts.append("atempo=0.5")
        ratio /= 0.5
    parts.append(f"atempo={ratio:.6f}")
    return ",".join(parts)


def _prepare_cover_image(
    job: ConversionJob,
    info: VideoInfo,
    crop: CropRect,
    padding: FramePadding,
    temp_dir: Path,
) -> Path:
    raw_cover = temp_dir / "cover_raw.png"
    ffmpeg = find_ffmpeg()
    total_w = crop.w + padding.left + padding.right
    total_h = crop.h + padding.top + padding.bottom

    if job.key_source == "video_frame":
        frame_idx = max(job.start_frame, min(job.key_frame_index, job.end_frame))
        full_png = temp_dir / "cover_full.png"
        run_command(
            [
                str(ffmpeg),
                "-y",
                "-i",
                str(info.path),
                "-vf",
                f"select=eq(n\\,{frame_idx})",
                "-frames:v",
                "1",
                str(full_png),
            ]
        )
        if not full_png.is_file():
            raise RuntimeError(f"无法从视频提取第 {frame_idx} 帧作为主图。")
        with Image.open(full_png) as pil_full:
            rgb = np.array(pil_full.convert("RGB"))
        composed = compose_cropped_frame_rgb(
            rgb,
            crop,
            bg_mode=job.bg_mode,
            bg_color=job.bg_color,
            bg_blur_sigma=job.bg_blur_sigma,
            padding=padding,
        )
        Image.fromarray(composed).save(raw_cover, format="PNG")
        return raw_cover

    if job.external_key_image is None or not job.external_key_image.is_file():
        raise FileNotFoundError("未选择有效的外部主要图片文件。")

    with Image.open(job.external_key_image) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        target_ratio = total_w / total_h
        src_ratio = img.width / img.height
        if abs(src_ratio - target_ratio) > 1e-3:
            if job.external_key_aspect_mode == "pad_fit":
                if src_ratio > target_ratio:
                    canvas_w = img.width
                    canvas_h = max(2, int(round(canvas_w / target_ratio)))
                else:
                    canvas_h = img.height
                    canvas_w = max(2, int(round(canvas_h * target_ratio)))
                rgb_ext = np.array(img)
                if job.bg_mode == "solid":
                    r, g, b = _parse_hex_rgb(job.bg_color)
                    canvas = np.empty((canvas_h, canvas_w, 3), dtype=np.uint8)
                    canvas[:, :] = (r, g, b)
                else:
                    canvas = _make_blurred_background(rgb_ext, canvas_w, canvas_h, job.bg_blur_sigma)
                ox = (canvas_w - img.width) // 2
                oy = (canvas_h - img.height) // 2
                canvas[oy : oy + img.height, ox : ox + img.width] = rgb_ext
                img = Image.fromarray(canvas)
            else:
                if src_ratio > target_ratio:
                    new_w = max(2, int(round(img.height * target_ratio)))
                    left = (img.width - new_w) // 2
                    img = img.crop((left, 0, left + new_w, img.height))
                else:
                    new_h = max(2, int(round(img.width / target_ratio)))
                    top = (img.height - new_h) // 2
                    img = img.crop((0, top, img.width, top + new_h))

        if job.external_key_match_video_res:
            img = img.resize((total_w, total_h), Image.Resampling.LANCZOS)
        img.save(raw_cover, format="PNG")
    return raw_cover


def _encode_audio_track(
    job: ConversionJob,
    info: VideoInfo,
    summary: TimelineSummary,
    temp_dir: Path,
) -> Path | None:
    if job.mute or not info.has_audio:
        return None

    ffmpeg = find_ffmpeg()
    start_sec = info.frame_to_seconds(job.start_frame)
    src_dur = summary.source_trimmed_duration
    out_dur = summary.output_duration
    audio_out = temp_dir / "audio.m4a"

    afilters: list[str] = []
    ratio = src_dur / out_dur if out_dur > 0 else 1.0
    if abs(ratio - 1.0) > 0.015:
        if job.audio_retime_mode == "speed_sync":
            afilters.append(_build_atempo_chain(ratio))
        else:
            fade_start = max(0.0, out_dur - 0.08)
            afilters.append(f"apad,atrim=0:{out_dur:.6f},afade=t=out:st={fade_start:.6f}:d=0.08")
    else:
        fade_start = max(0.0, out_dur - 0.04)
        afilters.append(f"apad,atrim=0:{out_dur:.6f},afade=t=out:st={fade_start:.6f}:d=0.04")

    cmd = [
        str(ffmpeg),
        "-y",
        "-ss",
        f"{start_sec:.6f}",
        "-t",
        f"{src_dur:.6f}",
        "-i",
        str(info.path),
        "-vn",
        "-af",
        ",".join(afilters),
        "-t",
        f"{out_dur:.6f}",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(audio_out),
    ]
    run_command(cmd)
    return audio_out if audio_out.is_file() else None


def _build_spatial_filter_graph(
    input_label: str,
    output_label: str,
    vw: int,
    vh: int,
    crop: CropRect,
    padding: FramePadding,
    bg_mode: str,
    bg_color: str,
    bg_blur_sigma: int,
    out_w: int,
    out_h: int,
) -> str:
    cw, ch = crop.w, crop.h
    pad = padding.normalized()
    total_w = cw + pad.left + pad.right
    total_h = ch + pad.top + pad.bottom

    need_scale = (out_w != total_w) or (out_h != total_h)
    scale_suffix = f",scale={out_w}:{out_h}:flags=lanczos" if need_scale else ""

    if not crop.is_out_of_bounds(vw, vh) and pad.is_zero():
        return f"[{input_label}]crop={cw}:{ch}:{crop.x}:{crop.y}{scale_suffix}[{output_label}]"

    ix1 = max(0, crop.x)
    iy1 = max(0, crop.y)
    ix2 = min(vw, crop.x + cw)
    iy2 = min(vh, crop.y + ch)
    iw = max(2, ix2 - ix1)
    ih = max(2, iy2 - iy1)
    dx = pad.left + (ix1 - crop.x)
    dy = pad.top + (iy1 - crop.y)

    if bg_mode == "solid":
        hex_col = "0x" + bg_color.strip().lstrip("#")
        bg_chain = f"[bgsrc]scale=2:2,drawbox=0:0:2:2:color={hex_col}:t=fill,scale={total_w}:{total_h}:flags=neighbor[bg]"
    else:
        scale_cover = max(total_w / float(vw), total_h / float(vh))
        sw = max(total_w, int(round((vw * scale_cover) / 2.0) * 2))
        sh = max(total_h, int(round((vh * scale_cover) / 2.0) * 2))
        dw = max(2, (total_w // 4) // 2 * 2)
        dh = max(2, (total_h // 4) // 2 * 2)
        sigma = max(1.5, float(bg_blur_sigma) / 4.0)
        bg_chain = (
            f"[bgsrc]scale={sw}:{sh}:flags=bilinear,crop={total_w}:{total_h},"
            f"scale={dw}:{dh}:flags=area,gblur=sigma={sigma:.2f}:steps=2,"
            f"scale={total_w}:{total_h}:flags=bilinear[bg]"
        )

    return (
        f"[{input_label}]split=2[bgsrc][fgsrc];"
        f"{bg_chain};"
        f"[fgsrc]crop={iw}:{ih}:{ix1}:{iy1}[fg];"
        f"[bg][fg]overlay={dx}:{dy}:format=auto{scale_suffix}[{output_label}]"
    )


def _video_codec_args(use_hw_encode: bool, bitrate_mbps: float) -> list[str]:
    kbps = max(500, int(round(float(bitrate_mbps) * 1000)))
    max_kbps = int(round(kbps * 1.25))
    buf_kbps = int(round(kbps * 2.0))
    if use_hw_encode and has_nvenc():
        return [
            "-dn",
            "-write_tmcd",
            "false",
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p5",
            "-rc",
            "vbr",
            "-b:v",
            f"{kbps}k",
            "-maxrate",
            f"{max_kbps}k",
            "-bufsize",
            f"{buf_kbps}k",
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "high",
            "-tag:v",
            "avc1",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-f",
            "mov",
        ]
    return [
        "-dn",
        "-write_tmcd",
        "false",
        "-c:v",
        "libx264",
        "-b:v",
        f"{kbps}k",
        "-maxrate",
        f"{max_kbps}k",
        "-bufsize",
        f"{buf_kbps}k",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-tag:v",
        "avc1",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-f",
        "mov",
    ]


def _render_intermediate_mov(
    job: ConversionJob,
    info: VideoInfo,
    crop: CropRect,
    padding: FramePadding,
    summary: TimelineSummary,
    audio_path: Path | None,
    temp_dir: Path,
    progress_cb: ProgressCallback | None,
    cancel_cb: CancelCallback | None,
) -> Path:
    ffmpeg = find_ffmpeg()
    inter_mov = temp_dir / "intermediate.mov"
    start_f = job.start_frame
    end_f_excl = job.end_frame + 1
    total_w = crop.w + padding.left + padding.right
    total_h = crop.h + padding.top + padding.bottom
    out_w, out_h = compute_scaled_video_dims(total_w, total_h, job.video_max_edge)
    vcodec = _video_codec_args(job.use_hw_encode, job.video_bitrate_mbps)

    if job.fps_mode == "rife_interpolate":
        in_frames = temp_dir / "rife_in"
        out_frames = temp_dir / "rife_out"
        in_frames.mkdir(parents=True, exist_ok=True)
        if progress_cb:
            progress_cb(15, f"正在提取 {summary.selected_frames} 帧待补帧画面 ({out_w}×{out_h})...")

        spatial = _build_spatial_filter_graph(
            "pre",
            "vout",
            info.width,
            info.height,
            crop,
            padding,
            job.bg_mode,
            job.bg_color,
            job.bg_blur_sigma,
            out_w,
            out_h,
        )
        fc = f"[0:v:0]trim=start_frame={start_f}:end_frame={end_f_excl},setpts=PTS-STARTPTS[pre];{spatial}"
        run_command(
            [
                str(ffmpeg),
                "-y",
                "-i",
                str(info.path),
                "-filter_complex",
                fc,
                "-map",
                "[vout]",
                "-fps_mode",
                "passthrough",
                str(in_frames / "%08d.png"),
            ]
        )

        if cancel_cb and cancel_cb():
            raise ConversionCancelled("用户已取消转换")

        interpolate_frames_rife(
            input_dir=in_frames,
            output_dir=out_frames,
            target_frames=summary.output_frames,
            model_name=job.rife_model,
            width=out_w,
            height=out_h,
            gpu_id=job.rife_gpu_id,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )

        if progress_cb:
            progress_cb(75, "正在编码补帧后的 QuickTime MOV 视频...")
        cmd = [
            str(ffmpeg),
            "-y",
            "-framerate",
            f"{summary.output_fps:.6f}",
            "-i",
            str(out_frames / "%08d.png"),
        ]
        if audio_path:
            cmd += ["-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "copy"]
        else:
            cmd += ["-map", "0:v:0", "-an"]
        cmd += vcodec + [str(inter_mov)]
        run_command(cmd)
        return inter_mov

    if progress_cb:
        progress_cb(30, f"正在编码视频轨道 ({out_w}×{out_h})...")

    spatial = _build_spatial_filter_graph(
        "pre",
        "vspatial",
        info.width,
        info.height,
        crop,
        padding,
        job.bg_mode,
        job.bg_color,
        job.bg_blur_sigma,
        out_w,
        out_h,
    )
    post_ops: list[str] = []
    if job.fps_mode == "raw_retime":
        ratio = info.fps / summary.output_fps
        post_ops.append(f"setpts={ratio:.8f}*PTS")
    elif job.fps_mode == "ffmpeg_minterpolate":
        post_ops.append(
            f"minterpolate=fps={summary.output_fps:.6f}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
        )

    if post_ops:
        fc = (
            f"[0:v:0]trim=start_frame={start_f}:end_frame={end_f_excl},setpts=PTS-STARTPTS[pre];"
            f"{spatial};[vspatial]{','.join(post_ops)}[vout]"
        )
    else:
        fc = (
            f"[0:v:0]trim=start_frame={start_f}:end_frame={end_f_excl},setpts=PTS-STARTPTS[pre];"
            f"{spatial.replace('[vspatial]', '[vout]')}"
        )

    cmd = [str(ffmpeg), "-y", "-i", str(info.path)]
    if audio_path:
        cmd += ["-i", str(audio_path), "-filter_complex", fc, "-map", "[vout]", "-map", "1:a:0", "-c:a", "copy"]
    else:
        cmd += ["-filter_complex", fc, "-map", "[vout]", "-an"]

    if job.fps_mode == "raw_retime":
        cmd += ["-fps_mode", "cfr", "-r", f"{summary.output_fps:.6f}"]
    elif job.fps_mode == "keep_original":
        cmd += ["-fps_mode", "passthrough"]

    cmd += vcodec + [str(inter_mov)]
    run_command(cmd)
    return inter_mov


def run_conversion(
    job: ConversionJob,
    progress_cb: ProgressCallback | None = None,
    cancel_cb: CancelCallback | None = None,
) -> ConversionResult:
    """Execute full conversion, Apple Live Photo metadata injection, verification, and export."""
    info = probe_video(job.video_path)
    crop = job.crop.normalized(info.width, info.height, allow_out_of_bounds=True)
    padding = job.padding.normalized()
    summary = compute_timeline_summary(info, job)
    asset_id = str(uuid.uuid4()).upper()
    custom_exif = job.exif_metadata
    effective_dt = (custom_exif.parsed_datetime() if custom_exif else None) or datetime.now()
    ext = "HEIC" if job.image_format == "heic" else "JPG"

    with tempfile.TemporaryDirectory(prefix="tolivephoto_") as tmp_str:
        temp_dir = Path(tmp_str)

        if cancel_cb and cancel_cb():
            raise ConversionCancelled("用户已取消转换")

        if progress_cb:
            progress_cb(5, "正在提取并生成主要图片 (封面)...")
        raw_cover = _prepare_cover_image(job, info, crop, padding, temp_dir)
        staged_img = temp_dir / f"live_cover.{ext}"
        write_live_image(
            raw_cover,
            staged_img,
            asset_id,
            creation_dt=effective_dt,
            custom_exif=custom_exif,
        )

        if progress_cb:
            progress_cb(10, "正在处理音轨..." if not job.mute and info.has_audio else "静音模式：跳过音轨...")
        audio_path = _encode_audio_track(job, info, summary, temp_dir)

        inter_mov = _render_intermediate_mov(
            job, info, crop, padding, summary, audio_path, temp_dir, progress_cb, cancel_cb
        )

        if cancel_cb and cancel_cb():
            raise ConversionCancelled("用户已取消转换")

        if progress_cb:
            progress_cb(85, "正在注入 Apple Live Photo UUID 与 timed metadata...")
        staged_mov = temp_dir / "live_video.MOV"
        actual_mov_info = probe_video(inter_mov)
        actual_dur = max(0.05, actual_mov_info.duration)
        still_time = min(max(0.0, summary.cover_still_time), max(0.0, actual_dur - 0.001))

        write_live_mov(
            source=inter_mov,
            output=staged_mov,
            asset_id=asset_id,
            still_time=still_time,
            duration=actual_dur,
            creation_dt=effective_dt,
            custom_exif=custom_exif,
        )

        if progress_cb:
            progress_cb(92, "正在校验实况配对元数据完整性...")
        verify_live_pair(staged_img, staged_mov)

        if progress_cb:
            progress_cb(96, "正在写入导出目录...")
        used_dcf_number: int | None = None
        if job.export_mode == "usb_dcim":
            img_dest, mov_dest, _stem, used_dcf_number = resolve_dcf_pair_paths(
                usb_root=job.output_root,
                preferred_number=job.dcf_number,
                source_stem=info.path.stem,
                image_ext=ext,
                prefix=job.dcf_prefix,
            )
            livp_dest: Path | None = None
        else:
            out_dir = Path(job.output_root)
            out_dir.mkdir(parents=True, exist_ok=True)
            stem = (job.folder_stem or info.path.stem).strip() or "LivePhoto"
            img_dest = out_dir / f"{stem}.{ext}"
            mov_dest = out_dir / f"{stem}.MOV"
            livp_dest = (out_dir / f"{stem}.livp") if job.also_pack_livp else None

        shutil.copy2(staged_img, img_dest)
        shutil.copy2(staged_mov, mov_dest)
        sync_pair_timestamps(img_dest, mov_dest, effective_dt)

        if livp_dest is not None:
            with zipfile.ZipFile(livp_dest, "w", compression=zipfile.ZIP_STORED) as zf:
                zf.write(img_dest, arcname=img_dest.name)
                zf.write(mov_dest, arcname=mov_dest.name)

    if progress_cb:
        progress_cb(100, "导出完成！")

    return ConversionResult(
        jpg_path=img_dest,
        mov_path=mov_dest,
        livp_path=livp_dest,
        summary=summary,
        asset_id=asset_id,
        still_time=still_time,
        used_dcf_number=used_dcf_number,
    )
