"""Dual-handle range timeline slider with X秒Y帧 labels (zero decimal points)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..core.models import format_frames_as_sec_frames, format_sec_frames


class RangeTimelineSlider(QWidget):
    rangeChanged = Signal(int, int)
    currentFrameChanged = Signal(int)

    _NONE = 0
    _START = 1
    _END = 2
    _PLAYHEAD = 3

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(66)
        self.setMouseTracking(True)
        self._total = 1
        self._start = 0
        self._end = 0
        self._current = 0
        self._key_frame = 0
        self._show_key_star = True
        self._output_duration = 0.0
        self._cover_still_time = 0.0
        self._output_fps = 24.0
        self._output_frames = 0
        self._drag_target = self._NONE

    def configure(self, total_frames: int, start: int, end: int, current: int, key_frame: int) -> None:
        self._total = max(1, total_frames)
        self._start = max(0, min(start, self._total - 1))
        self._end = max(self._start, min(end, self._total - 1))
        self._current = max(0, min(current, self._total - 1))
        self._key_frame = max(0, min(key_frame, self._total - 1))
        self.update()

    def set_timing_context(
        self,
        output_duration: float,
        cover_still_time: float,
        output_fps: float = 24.0,
        output_frames: int = 0,
    ) -> None:
        self._output_duration = max(0.0, output_duration)
        self._cover_still_time = max(0.0, cover_still_time)
        self._output_fps = max(1.0, output_fps)
        self._output_frames = max(0, output_frames)
        self.update()

    def set_key_frame(self, key_frame: int, show_star: bool = True) -> None:
        self._key_frame = max(0, min(key_frame, self._total - 1))
        self._show_key_star = show_star
        self.update()

    def set_current_frame(self, frame: int) -> None:
        self._current = max(0, min(frame, self._total - 1))
        self.update()

    def _track_rect(self) -> QRectF:
        return QRectF(20, 23, max(20, self.width() - 40), 10)

    def _frame_to_x(self, frame: int) -> float:
        tr = self._track_rect()
        if self._total <= 1:
            return tr.left()
        return tr.left() + (frame / (self._total - 1)) * tr.width()

    def _x_to_frame(self, x: float) -> int:
        tr = self._track_rect()
        if self._total <= 1 or tr.width() <= 0:
            return 0
        ratio = max(0.0, min(1.0, (x - tr.left()) / tr.width()))
        return int(round(ratio * (self._total - 1)))

    def paintEvent(self, event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tr = self._track_rect()
        small_font = QFont(self.font())
        small_font.setPixelSize(12)
        p.setFont(small_font)

        # Background track
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#D4E0F4"))
        p.drawRoundedRect(tr, 5, 5)

        sx = self._frame_to_x(self._start)
        ex = self._frame_to_x(self._end)
        span_w = max(4.0, ex - sx)
        p.setBrush(QColor("#A4C6E9"))
        p.drawRoundedRect(QRectF(sx, tr.top(), span_w, tr.height()), 5, 5)

        # Top row annotations (X秒Y帧, zero decimals)
        p.setPen(QColor("#4F5776"))
        p.drawText(
            QRectF(max(0.0, sx - 12), 2, 64, 17),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "0秒0帧",
        )

        dur_color = QColor("#642E6E") if self._output_duration > 3.0 else QColor("#7D85CC")
        p.setPen(dur_color)
        total_txt = (
            format_frames_as_sec_frames(self._output_frames, self._output_fps)
            if self._output_frames > 0
            else format_sec_frames(self._output_duration, self._output_fps)
        )
        p.drawText(
            QRectF(min(self.width() - 96, ex - 48), 2, 94, 17),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"总 {total_txt}",
        )

        if self._show_key_star:
            kx = self._frame_to_x(self._key_frame)
            star_txt = f"★{format_sec_frames(self._cover_still_time, self._output_fps)}"
            pill = QRectF(max(0.0, min(self.width() - 88, kx - 44)), 1, 88, 18)
            p.setPen(QPen(QColor("#DF9FE8"), 1))
            p.setBrush(QColor("#FADCFE"))
            p.drawRoundedRect(pill, 6, 6)
            p.setPen(QColor("#642E6E"))
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, star_txt)

        # 3秒0帧 reference marker inside active range
        if self._output_duration > 3.05 and span_w > 40:
            x_3s = sx + (3.0 / self._output_duration) * span_w
            p.setPen(QPen(QColor("#642E6E"), 1.5, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x_3s, tr.top() - 2), QPointF(x_3s, tr.bottom() + 2))

        # Bottom time ruler (integer seconds, zero decimals)
        if self._output_duration > 0.05 and span_w > 30:
            sec_step = 1 if self._output_duration <= 8.0 else 2
            sec_tick = 0
            last_label_x = -100.0
            while sec_tick <= int(self._output_duration) + 1:
                if sec_tick > self._output_duration + 0.02:
                    break
                tx = sx + (sec_tick / self._output_duration) * span_w
                p.setPen(QPen(QColor("#A4C6E9"), 1.2))
                p.drawLine(QPointF(tx, tr.bottom() + 4), QPointF(tx, tr.bottom() + 9))
                if tx - last_label_x >= 36:
                    p.setPen(QColor("#4F5776"))
                    p.drawText(
                        QRectF(tx - 18, tr.bottom() + 10, 36, 17),
                        Qt.AlignmentFlag.AlignCenter,
                        f"{sec_tick}秒",
                    )
                    last_label_x = tx
                sec_tick += sec_step

        # Start & End handles (#7D85CC)
        p.setPen(QPen(QColor("#FEFDF9"), 1.5))
        p.setBrush(QColor("#7D85CC"))
        p.drawRoundedRect(QRectF(sx - 5, tr.top() - 4, 10, tr.height() + 8), 4, 4)
        p.drawRoundedRect(QRectF(ex - 5, tr.top() - 4, 10, tr.height() + 8), 4, 4)

        # Playhead cursor
        cx = self._frame_to_x(self._current)
        p.setPen(QPen(QColor("#642E6E"), 2))
        p.drawLine(QPointF(cx, tr.top() - 6), QPointF(cx, tr.bottom() + 6))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        sx = self._frame_to_x(self._start)
        ex = self._frame_to_x(self._end)
        if abs(x - sx) <= 10:
            self._drag_target = self._START
        elif abs(x - ex) <= 10:
            self._drag_target = self._END
        else:
            self._drag_target = self._PLAYHEAD
            f = self._x_to_frame(x)
            self._current = f
            self.currentFrameChanged.emit(f)
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_target == self._NONE:
            return
        f = self._x_to_frame(event.position().x())
        if self._drag_target == self._START:
            self._start = min(f, self._end)
            self._current = self._start
            self.rangeChanged.emit(self._start, self._end)
            self.currentFrameChanged.emit(self._current)
        elif self._drag_target == self._END:
            self._end = max(f, self._start)
            self._current = self._end
            self.rangeChanged.emit(self._start, self._end)
            self.currentFrameChanged.emit(self._current)
        elif self._drag_target == self._PLAYHEAD:
            self._current = f
            self.currentFrameChanged.emit(f)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_target = self._NONE
