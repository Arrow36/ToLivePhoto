"""Video stream probing via ffprobe."""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

from .models import VideoInfo
from .tools import find_ffprobe, run_command


def _parse_fps(rate_str: str | None) -> tuple[int, int, float]:
    if not rate_str or rate_str == "0/0":
        return 24, 1, 24.0
    frac = Fraction(rate_str)
    if frac <= 0:
        return 24, 1, 24.0
    return frac.numerator, frac.denominator, float(frac)


def probe_video(path: Path) -> VideoInfo:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到视频文件: {path}")

    ffprobe = find_ffprobe()
    proc = run_command(
        [
            str(ffprobe),
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video_stream is None:
        raise ValueError(f"文件中不包含视频流: {path}")

    width = int(video_stream["width"])
    height = int(video_stream["height"])

    rotation = 0
    for sd in video_stream.get("side_data_list", []):
        if "rotation" in sd:
            rotation = int(round(float(sd["rotation"])))
    if rotation == 0 and "rotate" in video_stream.get("tags", {}):
        rotation = int(round(float(video_stream["tags"]["rotate"])))
    if abs(rotation) in (90, 270):
        width, height = height, width

    fps_str = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
    fps_num, fps_den, fps = _parse_fps(fps_str)

    raw_dur = video_stream.get("duration") or fmt.get("duration") or "0"
    duration = float(raw_dur)

    raw_frames = video_stream.get("nb_frames")
    if raw_frames and str(raw_frames).isdigit() and int(raw_frames) > 0:
        nb_frames = int(raw_frames)
    else:
        nb_frames = max(1, round(duration * fps))

    if duration <= 0 and fps > 0:
        duration = nb_frames / fps

    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    color_space = str(video_stream.get("color_space") or "bt709")

    return VideoInfo(
        path=path,
        width=width,
        height=height,
        fps_num=fps_num,
        fps_den=fps_den,
        fps=fps,
        nb_frames=nb_frames,
        duration=duration,
        has_audio=has_audio,
        color_space=color_space,
    )
