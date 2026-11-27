"""Data models, Polaroid instant film geometry, timeline calculation, and size estimation for ToLivePhoto."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .exif_io import ExifMetadata

FpsMode = Literal["keep_original", "raw_retime", "rife_interpolate", "ffmpeg_minterpolate"]
AudioRetimeMode = Literal["speed_sync", "trim_fade"]
KeyPhotoSource = Literal["video_frame", "external_image"]
ExternalKeyAspectMode = Literal["crop_fill", "pad_fit", "match_cover_aspect"]
ExportMode = Literal["usb_dcim", "folder"]
FolderNamingMode = Literal["suffix", "original", "prefix_seq", "custom"]
RifeMode = Literal["target_fps", "multiplier"]
RifePlaybackPolicy = Literal["keep_duration", "custom_fps"]
ImageFormat = Literal["heic", "jpg"]
BgMode = Literal["blur", "solid"]

IPHONE_LIVE_BITRATE_MBPS: float = 8.0


@dataclass(frozen=True, slots=True)
class PolaroidSpec:
    key: str
    label: str
    ratio_label: str
    sheet_w_mm: float
    sheet_h_mm: float
    img_w_mm: float
    img_h_mm: float
    left_mm: float
    top_mm: float
    right_mm: float
    bottom_mm: float


POLAROID_PRESETS: dict[str, PolaroidSpec] = {
    "mini": PolaroidSpec(
        key="mini",
        label="Instax Mini",
        ratio_label="3:4",
        sheet_w_mm=54.0,
        sheet_h_mm=86.0,
        img_w_mm=46.0,
        img_h_mm=62.0,
        left_mm=4.0,
        top_mm=4.5,
        right_mm=4.0,
        bottom_mm=19.5,
    ),
    "square": PolaroidSpec(
        key="square",
        label="Instax Square",
        ratio_label="1:1",
        sheet_w_mm=72.0,
        sheet_h_mm=86.0,
        img_w_mm=62.0,
        img_h_mm=62.0,
        left_mm=5.0,
        top_mm=6.0,
        right_mm=5.0,
        bottom_mm=18.0,
    ),
    "wide": PolaroidSpec(
        key="wide",
        label="Instax Wide",
        ratio_label="16:10",
        sheet_w_mm=108.0,
        sheet_h_mm=86.0,
        img_w_mm=99.0,
        img_h_mm=62.0,
        left_mm=4.5,
        top_mm=6.0,
        right_mm=4.5,
        bottom_mm=18.0,
    ),
    "polaroid_600": PolaroidSpec(
        key="polaroid_600",
        label="Polaroid 600",
        ratio_label="1:1",
        sheet_w_mm=88.0,
        sheet_h_mm=107.0,
        img_w_mm=79.0,
        img_h_mm=79.0,
        left_mm=4.5,
        top_mm=5.0,
        right_mm=4.5,
        bottom_mm=23.0,
    ),
}


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
class FramePadding:
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    def is_zero(self) -> bool:
        return self.left <= 0 and self.top <= 0 and self.right <= 0 and self.bottom <= 0

    def normalized(self) -> FramePadding:
        def _even(v: int) -> int:
            return max(0, int(round(v / 2.0) * 2))

        return FramePadding(
            left=_even(self.left),
            top=_even(self.top),
            right=_even(self.right),
            bottom=_even(self.bottom),
        )


def compute_custom_polaroid_padding(crop: CropRect) -> FramePadding:
    """Compute balanced Polaroid-style margins around an arbitrary user-cropped frame."""
    cw = max(2, int(crop.w))
    ch = max(2, int(crop.h))
    side = max(16, int(round(min(cw, ch) * 0.08)))
    top = side
    bottom = max(side * 2, int(round(ch * 0.24)))
    return FramePadding(left=side, top=top, right=side, bottom=bottom).normalized()


def compute_polaroid_layout(
    preset_key: str,
    video_w: int,
    video_h: int,
    current_crop: CropRect | None = None,
    preserve_crop_scale: bool = False,
) -> tuple[CropRect, FramePadding]:
    """Inner-crop video to film image window aspect ratio and compute asymmetric outer film margins."""
    vw = max(2, (int(video_w) // 2) * 2)
    vh = max(2, (int(video_h) // 2) * 2)

    if preset_key == "custom_crop":
        base = current_crop if current_crop is not None else CropRect(0, 0, vw, vh)
        crop = base.normalized(vw, vh, allow_out_of_bounds=True)
        return crop, compute_custom_polaroid_padding(crop)

    spec = POLAROID_PRESETS[preset_key]
    img_ratio = spec.img_w_mm / spec.img_h_mm

    if preserve_crop_scale and current_crop is not None:
        cur_w = max(2, int(current_crop.w))
        cur_h = max(2, int(current_crop.h))
        if cur_w / cur_h >= img_ratio:
            ch = cur_h
            cw = max(2, int(round(ch * img_ratio)))
        else:
            cw = cur_w
            ch = max(2, int(round(cw / img_ratio)))
        cx = current_crop.x + current_crop.w / 2.0
        cy = current_crop.y + current_crop.h / 2.0
        x = int(round(cx - cw / 2.0))
        y = int(round(cy - ch / 2.0))
        allow_oob = current_crop.is_out_of_bounds(vw, vh)
        if not allow_oob:
            x = max(0, min(x, vw - cw))
            y = max(0, min(y, vh - ch))
        crop = CropRect(x=x, y=y, w=cw, h=ch).normalized(vw, vh, allow_out_of_bounds=allow_oob)
    else:
        vid_ratio = vw / vh
        if vid_ratio >= img_ratio:
            ch = vh
            cw = int(round(ch * img_ratio))
        else:
            cw = vw
            ch = int(round(cw / img_ratio))

        cx = vw / 2.0
        cy = vh / 2.0
        if current_crop is not None and not current_crop.is_out_of_bounds(vw, vh):
            cx = current_crop.x + current_crop.w / 2.0
            cy = current_crop.y + current_crop.h / 2.0

        x = int(round(cx - cw / 2.0))
        y = int(round(cy - ch / 2.0))
        x = max(0, min(x, vw - cw))
        y = max(0, min(y, vh - ch))
        crop = CropRect(x=x, y=y, w=cw, h=ch).normalized(vw, vh, allow_out_of_bounds=False)

    px_per_mm = crop.w / spec.img_w_mm
    pad = FramePadding(
        left=int(round(spec.left_mm * px_per_mm)),
        top=int(round(spec.top_mm * px_per_mm)),
        right=int(round(spec.right_mm * px_per_mm)),
        bottom=int(round(spec.bottom_mm * px_per_mm)),
    ).normalized()
    return crop, pad


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
    padding: FramePadding = field(default_factory=FramePadding)
    polaroid_preset: str | None = None
    mute: bool = False
    fps_mode: FpsMode = "keep_original"
    custom_fps: float = 30.0
    rife_mode: RifeMode = "multiplier"
    rife_multiplier: int = 2
    rife_model: str = "rife-v4.6"
    rife_gpu_id: int = 0
    rife_playback_policy: RifePlaybackPolicy = "custom_fps"
    audio_retime_mode: AudioRetimeMode = "speed_sync"
    key_source: KeyPhotoSource = "video_frame"
    key_frame_index: int = 0
    external_key_image: Path | None = None
    external_key_position_pct: float = 50.0
    external_key_match_video_res: bool = False
    external_key_aspect_mode: ExternalKeyAspectMode = "crop_fill"
    export_mode: ExportMode = "usb_dcim"
    output_root: Path = Path(".")
    dcf_prefix: str = "IMG_"
    dcf_number: int | None = None
    folder_stem: str | None = None
    folder_naming_mode: FolderNamingMode = "suffix"
    folder_suffix: str = "_2live"
    folder_custom_prefix: str = "LIVE_"
    folder_seq_number: int = 1
    avoid_overwrite: bool = True
    also_pack_livp: bool = False
    image_format: ImageFormat = "heic"
    video_max_edge: int = 0
    video_bitrate_mbps: float = 24.0
    use_hw_encode: bool = True
    bg_mode: BgMode = "blur"
    bg_blur_sigma: int = 35
    bg_color: str = "#FFFFFF"
    exif_metadata: ExifMetadata | None = None


def format_frames_as_sec_frames(total_frames: int, fps: float) -> str:
    """Format a frame count at fps as 'X秒Y帧' with zero decimal points."""
    fps_int = max(1, int(round(fps if fps > 0 else 24.0)))
    frames = max(0, int(total_frames))
    secs = frames // fps_int
    rem = frames % fps_int
    return f"{secs}秒{rem}帧"


def format_sec_frames(seconds: float, fps: float) -> str:
    """Format seconds at fps as 'X秒Y帧' with zero decimal points."""
    fps_val = fps if fps > 0 else 24.0
    whole_frames = max(0, int(round(max(0.0, seconds) * fps_val)))
    return format_frames_as_sec_frames(whole_frames, fps_val)


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
        if job.rife_mode == "target_fps":
            output_fps = max(1.0, float(job.custom_fps))
            output_frames = 1 if selected_frames == 1 else max(2, int(round(selected_frames * output_fps / src_fps)))
        else:
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


def compute_3s_trim_around_key(info: VideoInfo, job: ConversionJob) -> tuple[int, int]:
    """Trim [start_frame, end_frame] to <= 3s centered on Key Photo without changing FPS."""
    total = max(1, info.nb_frames)
    src_fps = info.fps if info.fps > 0 else 24.0

    if job.fps_mode == "raw_retime":
        max_sel = max(1, int(3.0 * max(1.0, float(job.custom_fps))))
    elif job.fps_mode in ("rife_interpolate", "ffmpeg_minterpolate") and job.rife_mode == "multiplier":
        mult = max(2, int(job.rife_multiplier))
        out_fps = max(1.0, float(job.custom_fps)) if job.rife_playback_policy == "custom_fps" else src_fps * mult
        max_sel = max(1, int((3.0 * out_fps) // mult))
    else:
        max_sel = max(1, int(3.0 * src_fps))

    target_len = min(total, max_sel)
    anchor = max(0, min(job.key_frame_index, total - 1))
    half = target_len // 2
    new_start = max(0, anchor - half)
    new_end = new_start + target_len - 1
    if new_end >= total:
        new_end = total - 1
        new_start = max(0, new_end - target_len + 1)
    return new_start, new_end


def estimate_output_size_mb(info: VideoInfo, job: ConversionJob, summary: TimelineSummary) -> tuple[float, float, float]:
    """Return estimated (cover_mb, mov_mb, total_mb) from canvas dimensions, format, duration, and bitrate."""
    crop = job.crop.normalized(info.width, info.height, allow_out_of_bounds=True)
    pad = job.padding.normalized()
    canvas_w = crop.w + pad.left + pad.right
    canvas_h = crop.h + pad.top + pad.bottom

    pixels = canvas_w * canvas_h
    bytes_per_px = 0.42 if job.image_format == "heic" else 0.85
    img_mb = max(0.12, (pixels * bytes_per_px) / (1024.0 * 1024.0))

    dur = max(0.1, summary.output_duration)
    video_mb = (max(0.5, float(job.video_bitrate_mbps)) * dur) / 8.0
    audio_mb = (0.024 * dur) if (not job.mute and info.has_audio) else 0.0
    mov_mb = max(0.15, video_mb + audio_mb + 0.05)
    return img_mb, mov_mb, img_mb + mov_mb


def _sanitize_stem(raw: str, fallback: str) -> str:
    cleaned = "".join(c for c in raw.strip() if c not in '<>:"/\\|?*').strip()
    return cleaned or fallback


def build_folder_stem(source_stem: str, job: ConversionJob) -> str:
    """Build target filename stem for folder export from naming mode."""
    if job.folder_stem is not None:
        return _sanitize_stem(job.folder_stem, "LivePhoto")
    if job.folder_naming_mode == "original":
        return _sanitize_stem(source_stem, "LivePhoto")
    if job.folder_naming_mode == "prefix_seq":
        pfx = _sanitize_stem(job.folder_custom_prefix, "LIVE_")
        return f"{pfx}{max(1, int(job.folder_seq_number)):04d}"
    if job.folder_naming_mode == "custom":
        return _sanitize_stem(job.folder_custom_prefix, source_stem or "LivePhoto")
    sfx = _sanitize_stem(job.folder_suffix, "_2live")
    return f"{_sanitize_stem(source_stem, 'LivePhoto')}{sfx}"


def _stem_exists(out_dir: Path, stem: str, ext: str) -> bool:
    return any((out_dir / f"{stem}.{e}").exists() for e in {ext, "HEIC", "JPG", "MOV"})


def resolve_folder_output_paths(
    out_dir: Path,
    source_stem: str,
    image_ext: str,
    job: ConversionJob,
) -> tuple[Path, Path, Path | None, str]:
    """Resolve collision-safe folder export paths for HEIC/JPG, MOV, and optional LIVP."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = image_ext.upper().lstrip(".")
    base_stem = build_folder_stem(source_stem, job)
    stem = base_stem

    if job.avoid_overwrite and job.folder_stem is None:
        if job.folder_naming_mode == "prefix_seq":
            pfx = _sanitize_stem(job.folder_custom_prefix, "LIVE_")
            seq = max(1, int(job.folder_seq_number))
            while _stem_exists(out_dir, f"{pfx}{seq:04d}", ext):
                seq += 1
            stem = f"{pfx}{seq:04d}"
        else:
            counter = 1
            while _stem_exists(out_dir, stem, ext):
                stem = f"{base_stem}_{counter}"
                counter += 1

    img_dest = out_dir / f"{stem}.{ext}"
    mov_dest = out_dir / f"{stem}.MOV"
    livp_dest = (out_dir / f"{stem}.livp") if job.also_pack_livp else None
    return img_dest, mov_dest, livp_dest, stem


def preview_output_filename(info: VideoInfo, job: ConversionJob) -> str:
    """Return human-readable preview of target output filename pair."""
    ext = "HEIC" if job.image_format == "heic" else "JPG"
    if job.export_mode == "usb_dcim":
        if job.dcf_number is not None:
            num = job.dcf_number
            pfx = (job.dcf_prefix or "IMG_").upper()[:4].ljust(4, "_")
            return f"DCIM/100APPLE/{pfx}{num:04d}.{ext} + .MOV"
        stem = build_folder_stem(info.path.stem, job)
        return f"DCIM/100APPLE/{stem}.{ext} + .MOV"
    stem = build_folder_stem(info.path.stem, job)
    return f"{stem}.{ext} + .MOV"

