"""Hardware-accelerated sequential FFmpeg proxy frame provider for zero-stutter playback."""
from __future__ import annotations

import subprocess
from collections import OrderedDict
from pathlib import Path

import cv2
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage

from ..core.models import VideoInfo
from ..core.pipeline import compute_scaled_video_dims
from ..core.tools import _creation_flags, find_ffmpeg


class _ProxyDecodeThread(QThread):
    frameDecoded = Signal(int, object)
    finishedAll = Signal(int)

    def __init__(self, video_path: Path, proxy_w: int, proxy_h: int, max_frames: int) -> None:
        super().__init__()
        self._video_path = video_path
        self._pw = proxy_w
        self._ph = proxy_h
        self._max_frames = max_frames
        self._abort = False

    def abort(self) -> None:
        self._abort = True

    def run(self) -> None:
        try:
            ffmpeg = find_ffmpeg()
        except FileNotFoundError:
            return

        cmd = [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-hwaccel",
            "auto",
            "-i",
            str(self._video_path),
            "-map",
            "0:v:0",
            "-vf",
            f"scale={self._pw}:{self._ph}:flags=bilinear",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=_creation_flags(),
        )
        assert proc.stdout is not None
        frame_bytes = self._pw * self._ph * 3
        idx = 0
        try:
            while not self._abort and idx < self._max_frames:
                chunk = proc.stdout.read(frame_bytes)
                if len(chunk) < frame_bytes:
                    break
                qimg = QImage(chunk, self._pw, self._ph, self._pw * 3, QImage.Format.Format_RGB888).copy()
                self.frameDecoded.emit(idx, qimg)
                idx += 1
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self.finishedAll.emit(idx)


class PreviewFrameProvider(QObject):
    """Provides instant proxy frames decoded in background via FFmpeg -hwaccel auto."""

    proxyReady = Signal()
    frameAvailable = Signal(int)

    MAX_PROXY_LONG_EDGE = 960
    PRELOAD_LIMIT = 600

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._info: VideoInfo | None = None
        self._proxy_w = 0
        self._proxy_h = 0
        self._frames: OrderedDict[int, QImage] = OrderedDict()
        self._thread: _ProxyDecodeThread | None = None
        self._cap: cv2.VideoCapture | None = None

    @property
    def proxy_size(self) -> tuple[int, int]:
        return self._proxy_w, self._proxy_h

    def load(self, info: VideoInfo) -> None:
        self.stop()
        self._info = info
        self._frames.clear()
        self._proxy_w, self._proxy_h = compute_scaled_video_dims(
            info.width, info.height, self.MAX_PROXY_LONG_EDGE
        )
        self._cap = cv2.VideoCapture(str(info.path))
        self._read_fallback_frame(0)

        if info.nb_frames <= self.PRELOAD_LIMIT:
            self._thread = _ProxyDecodeThread(info.path, self._proxy_w, self._proxy_h, info.nb_frames)
            self._thread.frameDecoded.connect(self._on_frame_decoded)
            self._thread.finishedAll.connect(lambda _n: self.proxyReady.emit())
            self._thread.start()

    def _on_frame_decoded(self, idx: int, qimg: QImage) -> None:
        self._frames[idx] = qimg
        self.frameAvailable.emit(idx)

    def _read_fallback_frame(self, frame_idx: int) -> QImage | None:
        if self._cap is None or self._info is None:
            return None
        frame_idx = max(0, min(frame_idx, max(0, self._info.nb_frames - 1)))
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, bgr = self._cap.read()
        if not ok or bgr is None:
            return None
        if self._proxy_w > 0 and self._proxy_h > 0:
            bgr = cv2.resize(bgr, (self._proxy_w, self._proxy_h), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
        self._frames[frame_idx] = qimg
        while len(self._frames) > 650:
            self._frames.popitem(last=False)
        return qimg

    def get_frame(self, frame_idx: int) -> QImage | None:
        if self._info is None:
            return None
        frame_idx = max(0, min(frame_idx, max(0, self._info.nb_frames - 1)))
        if frame_idx in self._frames:
            return self._frames[frame_idx]
        return self._read_fallback_frame(frame_idx)

    def stop(self) -> None:
        if self._thread is not None:
            self._thread.abort()
            self._thread.wait(1500)
            self._thread = None
        if self._cap is not None:
            self._cap.release()
            self._cap = None
