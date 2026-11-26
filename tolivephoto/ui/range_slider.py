"""Dual-handle range timeline slider styled in the ToLivePhoto pastel palette."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget


class RangeTimelineSlider(QWidget):
    rangeChanged = Signal(int, int)
    currentFrameChanged = Signal(int)

    _NONE = 0
    _START = 1
    _END = 2
    _PLAYHEAD = 3

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(64)
        self.setMouseTracking(True)
        self._total = 1
        self._start = 0
        self._end = 0
        self._current = 0
        self._key_frame = 0
        self._show_key_star = True
        self._output_duration = 0.0
        self._cover_still_time = 0.0
        self._drag_target = self._NONE

    def configure(self, total_frames: int, start: int, end: int, current: int, key_frame: int) -> None:
        self._total = max(1, total_frames)
        self._start = max(0, min(start, self._total - 1))
        self._end = max(self._start, min(end, self._total - 1))
        self._current = max(0, min(current, self._total - 1))
        self._key_frame = max(0, min(key_frame, self._total - 1))
        self.update()

    def set_timing_context(self, output_duration: float, cover_still_time: float) -> None:
        self._output_duration = max(0.0, output_duration)
        self._cover_still_time = max(0.0, cover_still_time)
        self.update()

    def set_key_frame(self, key_frame: int, show_star: bool = True) -> None:
        self._key_frame = max(0, min(key_frame, self._total - 1))
        self._show_key_star = show_star
        self.update()

    def set_current_frame(self, frame: int) -> None:
        self._current = max(0, min(frame, self._total - 1))
        self.update()

    def _track_rect(self) -> QRectF:
        return QRectF(20, 22, max(20, self.width() - 40), 10)

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
        small_font.setPixelSize(11)
        p.setFont(small_font)

        # Background track
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#DCE6F7"))
        p.drawRoundedRect(tr, 5, 5)

        sx = self._frame_to_x(self._start)
        ex = self._frame_to_x(self._end)
        span_w = max(4.0, ex - sx)
        p.setBrush(QColor("#A4C6E9"))
        p.drawRoundedRect(QRectF(sx, tr.top(), span_w, tr.height()), 5, 5)

        # Top row annotations
        p.setPen(QColor("#5C6484"))
        p.drawText(QRectF(max(0.0, sx - 18), 2, 46, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "0.00s")

        dur_color = QColor("#6B3475") if self._output_duration > 3.0 else QColor("#838BCC")
        p.setPen(dur_color)
        p.drawText(
            QRectF(min(self.width() - 78, ex - 38), 2, 76, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"总 {self._output_duration:.2f}s",
        )

        if self._show_key_star:
            kx = self._frame_to_x(self._key_frame)
            pill = QRectF(max(0.0, min(self.width() - 68, kx - 34)), 1, 68, 17)
            p.setPen(QPen(QColor("#E4AEEC"), 1))
            p.setBrush(QColor("#FADCFE"))
            p.drawRoundedRect(pill, 6, 6)
            p.setPen(QColor("#6B3475"))
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, f"★{self._cover_still_time:.2f}s")

        # 3.0s reference marker inside active range
        if self._output_duration > 3.05 and span_w > 40:
            x_3s = sx + (3.0 / self._output_duration) * span_w
            p.setPen(QPen(QColor("#6B3475"), 1.5, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x_3s, tr.top() - 2), QPointF(x_3s, tr.bottom() + 2))

        # Bottom time ruler
        if self._output_duration > 0.05 and span_w > 30:
            step = 0.5 if self._output_duration <= 4.0 else (1.0 if self._output_duration <= 10.0 else 2.0)
            t = 0.0
            last_label_x = -100.0
            while t <= self._output_duration + 1e-4:
                tx = sx + (t / self._output_duration) * span_w
                p.setPen(QPen(QColor("#A4C6E9"), 1.2))
                p.drawLine(QPointF(tx, tr.bottom() + 4), QPointF(tx, tr.bottom() + 9))
                if tx - last_label_x >= 38:
                    p.setPen(QColor("#5C6484"))
                    p.drawText(
                        QRectF(tx - 18, tr.bottom() + 10, 36, 16),
                        Qt.AlignmentFlag.AlignCenter,
                        f"{t:.1f}s",
                    )
                    last_label_x = tx
                t += step

        # Start & End handles (#838BCC)
        p.setPen(QPen(QColor("#FEFDF9"), 1.5))
        p.setBrush(QColor("#838BCC"))
        p.drawRoundedRect(QRectF(sx - 5, tr.top() - 4, 10, tr.height() + 8), 4, 4)
        p.drawRoundedRect(QRectF(ex - 5, tr.top() - 4, 10, tr.height() + 8), 4, 4)

        # Playhead cursor
        cx = self._frame_to_x(self._current)
        p.setPen(QPen(QColor("#6B3475"), 2))
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
