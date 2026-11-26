"""Data models and timeline calculation for ToLivePhoto."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

FpsMode = Literal["keep_original", "raw_retime", "rife_interpolate", "ffmpeg_minterpolate"]
AudioRetimeMode = Literal["speed_sync", "trim_fade"]
KeyPhotoSource = Literal["video_frame", "external_image"]
ExportMode = Literal["usb_dcim", "folder"]
RifePlaybackPolicy = Literal["keep_duration", "custom_fps"]
ImageFormat = Literal["heic", "jpg"]
BgMode = Literal["blur", "solid"]


@dataclass(frozen=True, slots=True)
class VideoInfo:
    path: Path
    width: int
    height: int
    fps_num: int
    fps_den: int
    fps: float
    nb_frames: int
    duration: float
    has_audio: bool
    color_space: str = "bt709"

    def frame_to_seconds(self, frame_idx: int) -> float:
        if self.fps <= 0:
            return 0.0
        return max(0.0, frame_idx / self.fps)


@dataclass(frozen=True, slots=True)
class CropRect:
    x: int
    y: int
    w: int
    h: int

    def normalized(
        self,
        max_w: int,
        max_h: int,
        allow_out_of_bounds: bool = True,
    ) -> CropRect:
        """Enforce even width/height and valid overlap with the source frame."""
        max_w = max(2, (max_w // 2) * 2)
        max_h = max(2, (max_h // 2) * 2)

        if not allow_out_of_bounds:
            x = max(0, min(int(self.x), max_w - 2))
            y = max(0, min(int(self.y), max_h - 2))
            w = max(2, min(int(self.w), max_w - x))
            h = max(2, min(int(self.h), max_h - y))
            w = max(2, (w // 2) * 2)
            h = max(2, (h // 2) * 2)
            if x + w > max_w:
                x = max(0, max_w - w)
            if y + h > max_h:
                y = max(0, max_h - h)
            return CropRect(x=x, y=y, w=w, h=h)

        w = max(64, min(int(self.w), max_w * 3))
        h = max(64, min(int(self.h), max_h * 3))
        w = max(2, (w // 2) * 2)
        h = max(2, (h // 2) * 2)

        min_overlap = min(64, max_w, max_h)
        x = int(round(self.x))
        y = int(round(self.y))
        x = max(min_overlap - w, min(x, max_w - min_overlap))
        y = max(min_overlap - h, min(y, max_h - min_overlap))
        x = int(round(x / 2.0) * 2)
        y = int(round(y / 2.0) * 2)
        return CropRect(x=x, y=y, w=w, h=h)

    def is_out_of_bounds(self, max_w: int, max_h: int) -> bool:
        return self.x < 0 or self.y < 0 or (self.x + self.w) > max_w or (self.y + self.h) > max_h

    def is_full_frame(self, max_w: int, max_h: int) -> bool:
        norm = self.normalized(max_w, max_h, allow_out_of_bounds=True)
        return norm.x == 0 and norm.y == 0 and norm.w == (max_w // 2) * 2 and norm.h == (max_h // 2) * 2


@dataclass(frozen=True, slots=True)
class TimelineSummary:
    selected_frames: int
    output_frames: int
    output_fps: float
    output_duration: float
    cover_still_time: float
    source_trimmed_duration: float


@dataclass(slots=True)
class ConversionJob:
    video_path: Path
    start_frame: int
    end_frame: int
    crop: CropRect
    mute: bool = False
    fps_mode: FpsMode = "keep_original"
    custom_fps: float = 30.0
    rife_multiplier: int = 2
    rife_model: str = "rife-v4.25"
    rife_playback_policy: RifePlaybackPolicy = "keep_duration"
    audio_retime_mode: AudioRetimeMode = "speed_sync"
    key_source: KeyPhotoSource = "video_frame"
    key_frame_index: int = 0
    external_key_image: Path | None = None
    external_key_position_pct: float = 50.0
    external_key_match_video_res: bool = False
    export_mode: ExportMode = "usb_dcim"
    output_root: Path = Path(".")
    dcf_prefix: str = "IMG_"
    dcf_number: int | None = None
    folder_stem: str | None = None
    also_pack_livp: bool = False
    image_format: ImageFormat = "heic"
    video_max_edge: int = 0  # 0 = unscaled original dimensions by default
    use_hw_encode: bool = True
    bg_mode: BgMode = "blur"
    bg_blur_sigma: int = 35
    bg_color: str = "#FEFDF9"


def compute_timeline_summary(info: VideoInfo, job: ConversionJob) -> TimelineSummary:
    """Compute exact output frame count, FPS, duration, and cover anchor time."""
    start_f = max(0, min(job.start_frame, max(0, info.nb_frames - 1)))
    end_f = max(start_f, min(job.end_frame, max(0, info.nb_frames - 1)))
    selected_frames = max(1, end_f - start_f + 1)
    src_fps = info.fps if info.fps > 0 else 24.0
    source_trimmed_duration = selected_frames / src_fps

    if job.fps_mode == "keep_original":
        output_frames = selected_frames
        output_fps = src_fps
    elif job.fps_mode == "raw_retime":
        output_frames = selected_frames
        output_fps = max(1.0, float(job.custom_fps))
    elif job.fps_mode in ("rife_interpolate", "ffmpeg_minterpolate"):
        mult = max(2, int(job.rife_multiplier))
        output_frames = 1 if selected_frames == 1 else selected_frames * mult
        if job.rife_playback_policy == "custom_fps":
            output_fps = max(1.0, float(job.custom_fps))
        else:
            output_fps = src_fps * mult
    else:
        output_frames = selected_frames
        output_fps = src_fps

    output_duration = output_frames / output_fps

    if job.key_source == "video_frame":
        rel_frame = max(0, min(job.key_frame_index - start_f, selected_frames - 1))
        ratio = rel_frame / max(1, selected_frames - 1) if selected_frames > 1 else 0.0
    else:
        ratio = max(0.0, min(1.0, job.external_key_position_pct / 100.0))

    max_still = max(0.0, output_duration - (1.0 / output_fps))
    cover_still_time = min(max_still, max(0.0, ratio * output_duration))

    return TimelineSummary(
        selected_frames=selected_frames,
        output_frames=output_frames,
        output_fps=output_fps,
        output_duration=output_duration,
        cover_still_time=cover_still_time,
        source_trimmed_duration=source_trimmed_duration,
    )
