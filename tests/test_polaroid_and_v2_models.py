"""Tests for Polaroid presets, X秒Y帧 formatting, 3s Key-trim, bitrate/size estimator, and RIFE 30fps default."""
from __future__ import annotations

from pathlib import Path

from tolivephoto.core.models import (
    POLAROID_PRESETS,
    ConversionJob,
    CropRect,
    VideoInfo,
    compute_3s_trim_around_key,
    compute_polaroid_layout,
    compute_timeline_summary,
    estimate_output_size_mb,
    format_frames_as_sec_frames,
    format_sec_frames,
)


def _dummy_info(nb_frames: int = 120, fps: float = 24.0) -> VideoInfo:
    return VideoInfo(
        path=Path("dummy.mp4"),
        width=2160,
        height=3240,
        fps_num=int(fps),
        fps_den=1,
        fps=fps,
        nb_frames=nb_frames,
        duration=nb_frames / fps,
        has_audio=True,
    )


def test_polaroid_presets_geometry() -> None:
    for key, spec in POLAROID_PRESETS.items():
        crop, pad = compute_polaroid_layout(key, 2160, 3240)
        assert crop.w % 2 == 0 and crop.h % 2 == 0
        assert pad.left % 2 == 0 and pad.top % 2 == 0 and pad.right % 2 == 0 and pad.bottom % 2 == 0
        assert pad.bottom > pad.top  # Authentic Polaroid / Instax wide chin at bottom
        actual_img_ratio = crop.w / crop.h
        expected_img_ratio = spec.img_w_mm / spec.img_h_mm
        assert abs(actual_img_ratio - expected_img_ratio) < 0.02


def test_sec_frames_format_has_no_decimal_points() -> None:
    assert format_frames_as_sec_frames(59, 24.0) == "2秒11帧"
    assert format_frames_as_sec_frames(72, 24.0) == "3秒0帧"
    assert format_sec_frames(0.0, 24.0) == "0秒0帧"
    assert "." not in format_sec_frames(1.75, 30.0)


def test_rife_default_30fps_and_keep_original_default() -> None:
    info = _dummy_info(nb_frames=59, fps=24.0)
    job = ConversionJob(video_path=Path("dummy.mp4"), start_frame=0, end_frame=58, crop=CropRect(0, 0, 2160, 3240))
    assert job.fps_mode == "keep_original"
    assert job.custom_fps == 30.0
    assert job.rife_mode == "target_fps"

    job.fps_mode = "rife_interpolate"
    s = compute_timeline_summary(info, job)
    assert s.output_fps == 30.0
    assert s.output_frames == 74  # 59 * 30 / 24 = 73.75 -> 74 frames keeping original speed


def test_compute_3s_trim_around_key_preserves_fps() -> None:
    info = _dummy_info(nb_frames=240, fps=24.0)  # 10s video
    job = ConversionJob(
        video_path=Path("dummy.mp4"),
        start_frame=0,
        end_frame=239,
        crop=CropRect(0, 0, 2160, 3240),
        fps_mode="keep_original",
        key_frame_index=120,
    )
    ns, ne = compute_3s_trim_around_key(info, job)
    assert ns <= 120 <= ne
    assert (ne - ns + 1) == 72  # 3 seconds * 24fps = 72 frames
    assert job.fps_mode == "keep_original"


def test_estimate_output_size_scales_with_bitrate() -> None:
    info = _dummy_info(nb_frames=59, fps=24.0)
    job_low = ConversionJob(
        video_path=Path("dummy.mp4"),
        start_frame=0,
        end_frame=58,
        crop=CropRect(0, 0, 2160, 3240),
        video_bitrate_mbps=10.0,
    )
    job_high = ConversionJob(
        video_path=Path("dummy.mp4"),
        start_frame=0,
        end_frame=58,
        crop=CropRect(0, 0, 2160, 3240),
        video_bitrate_mbps=40.0,
    )
    s = compute_timeline_summary(info, job_low)
    _, mov_low, tot_low = estimate_output_size_mb(info, job_low, s)
    _, mov_high, tot_high = estimate_output_size_mb(info, job_high, s)
    assert mov_high > mov_low * 2.5
    assert tot_high > tot_low
