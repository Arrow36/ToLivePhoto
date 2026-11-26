"""Interactive canvas with wheel zoom, left-drag pan, right-click menu, and manual crop toggle."""
from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QContextMenuEvent, QImage, QMouseEvent, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import QWidget

from ..core.models import CropRect


class CropPreviewWidget(QWidget):
    cropChanged = Signal(object)  # CropRect
    zoomChanged = Signal(float)
    contextMenuRequested = Signal(object)  # QPoint globalPos

    HANDLE_NONE = 0
    HANDLE_PAN = 1
    HANDLE_MOVE = 2
    HANDLE_TL = 3
    HANDLE_TR = 4
    HANDLE_BL = 5
    HANDLE_BR = 6
    HANDLE_L = 7
    HANDLE_R = 8
    HANDLE_T = 9
    HANDLE_B = 10

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(460, 400)
        self.setMouseTracking(True)

        self._qimage: QImage | None = None
        self._pixmap: QPixmap | None = None
        self._blur_cache_key: tuple[int, int, int, int, int] | None = None
        self._blur_pixmap: QPixmap | None = None
        self._frame_seq: int = 0

        self._video_w: int = 0
        self._video_h: int = 0
        self._crop = CropRect(0, 0, 2, 2)
        self._aspect_ratio: float | None = None
        self._bg_mode: str = "blur"
        self._bg_color: str = "#FEFDF9"
        self._bg_blur_sigma: int = 35
        self._backdrop_hex: str = "#E6ECF8"

        self._crop_editing: bool = False  # Hidden until user clicks "裁切画面"
        self._zoom: float = 1.0
        self._pan = QPointF(0.0, 0.0)

        self._drag_mode: int = self.HANDLE_NONE
        self._drag_start_pos = QPointF()
        self._drag_start_pan = QPointF()
        self._drag_start_crop = CropRect(0, 0, 2, 2)
        self._drag_workspace: tuple[float, float, float] | None = None

    def set_frame(self, qimage: QImage | None, video_w: int, video_h: int, frame_idx: int = 0) -> None:
        self._video_w = video_w
        self._video_h = video_h
        self._qimage = qimage
        self._frame_seq = frame_idx
        self._blur_cache_key = None
        self._pixmap = QPixmap.fromImage(qimage) if (qimage is not None and not qimage.isNull()) else None
        self.update()

    def set_crop_editing(self, enabled: bool) -> None:
        self._crop_editing = bool(enabled)
        self.update()

    def is_crop_editing(self) -> bool:
        return self._crop_editing

    def set_backdrop_color(self, hex_color: str) -> None:
        self._backdrop_hex = hex_color
        self.update()

    def set_background_style(self, bg_mode: str, bg_color: str, bg_blur_sigma: int) -> None:
        self._bg_mode = bg_mode
        self._bg_color = bg_color
        self._bg_blur_sigma = bg_blur_sigma
        self._blur_cache_key = None
        self.update()

    def set_crop(self, crop: CropRect, emit: bool = False) -> None:
        if self._video_w > 0 and self._video_h > 0:
            self._crop = crop.normalized(self._video_w, self._video_h, allow_out_of_bounds=True)
        else:
            self._crop = crop
        self.update()
        if emit:
            self.cropChanged.emit(self._crop)

    def get_crop(self) -> CropRect:
        return self._crop

    def get_zoom(self) -> float:
        return self._zoom

    def set_zoom(self, zoom: float, anchor_pos: QPointF | None = None) -> None:
        new_zoom = max(0.25, min(8.0, float(zoom)))
        if abs(new_zoom - self._zoom) < 1e-4:
            return
        if anchor_pos is not None:
            cx, cy = self.width() / 2.0, self.height() / 2.0
            factor = new_zoom / self._zoom
            dx = anchor_pos.x() - cx
            dy = anchor_pos.y() - cy
            self._pan = QPointF(
                dx - (dx - self._pan.x()) * factor,
                dy - (dy - self._pan.y()) * factor,
            )
        self._zoom = new_zoom
        self.zoomChanged.emit(self._zoom)
        self.update()

    def reset_view(self) -> None:
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self.zoomChanged.emit(self._zoom)
        self.update()

    def apply_aspect_fit(self, ratio: float | None, mode: str = "inside", margin_pct: float = 0.0) -> None:
        self._aspect_ratio = ratio
        if self._video_w <= 0 or self._video_h <= 0:
            return
        r = ratio if ratio is not None else (self._video_w / self._video_h)
        vid_ratio = self._video_w / self._video_h

        if mode == "pad":
            if vid_ratio >= r:
                w = self._video_w
                h = int(round(w / r))
            else:
                h = self._video_h
                w = int(round(h * r))
            if margin_pct > 0:
                w = int(round(w * (1.0 + margin_pct / 100.0)))
                h = int(round(h * (1.0 + margin_pct / 100.0)))
            x = (self._video_w - w) // 2
            y = (self._video_h - h) // 2
        else:
            if vid_ratio >= r:
                h = self._video_h
                w = int(round(h * r))
            else:
                w = self._video_w
                h = int(round(w / r))
            x = (self._video_w - w) // 2
            y = (self._video_h - h) // 2
        self.set_crop(CropRect(x, y, w, h), emit=True)

    def _compute_workspace(self) -> tuple[float, float, float]:
        """Compute (origin_x, origin_y, scale) incorporating zoom & pan."""
        if self._drag_workspace is not None:
            return self._drag_workspace

        if self._crop_editing:
            ux1 = min(0, self._crop.x)
            uy1 = min(0, self._crop.y)
            ux2 = max(self._video_w, self._crop.x + self._crop.w)
            uy2 = max(self._video_h, self._crop.y + self._crop.h)
        else:
            ux1, uy1 = self._crop.x, self._crop.y
            ux2, uy2 = self._crop.x + self._crop.w, self._crop.y + self._crop.h

        uw = max(10.0, float(ux2 - ux1))
        uh = max(10.0, float(uy2 - uy1))
        pad_x = max(20.0, self.width() * 0.06)
        pad_y = max(20.0, self.height() * 0.06)
        avail_w = max(40.0, self.width() - 2 * pad_x)
        avail_h = max(40.0, self.height() - 2 * pad_y)
        base_scale = min(avail_w / uw, avail_h / uh)
        scale = base_scale * self._zoom

        cx = self.width() / 2.0 + self._pan.x()
        cy = self.height() / 2.0 + self._pan.y()
        ox = cx - ((ux1 + ux2) / 2.0) * scale
        oy = cy - ((uy1 + uy2) / 2.0) * scale
        return (ox, oy, scale)

    def _video_widget_rect(self, ws: tuple[float, float, float]) -> QRectF:
        ox, oy, scale = ws
        return QRectF(ox, oy, self._video_w * scale, self._video_h * scale)

    def _crop_widget_rect(self, ws: tuple[float, float, float]) -> QRectF:
        ox, oy, scale = ws
        return QRectF(
            ox + self._crop.x * scale,
            oy + self._crop.y * scale,
            self._crop.w * scale,
            self._crop.h * scale,
        )

    def _get_blurred_bg_pixmap(self, target_w: int, target_h: int) -> QPixmap | None:
        if self._qimage is None or target_w < 4 or target_h < 4:
            return None
        key = (self._frame_seq, target_w, target_h, self._bg_blur_sigma, self._crop.w)
        if self._blur_cache_key == key and self._blur_pixmap is not None:
            return self._blur_pixmap

        img = self._qimage.convertToFormat(QImage.Format.Format_RGB888)
        ptr = img.bits()
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((img.height(), img.bytesPerLine()))
        rgb = arr[:, : img.width() * 3].reshape((img.height(), img.width(), 3))

        vh, vw = rgb.shape[:2]
        scale = max(target_w / float(vw), target_h / float(vh))
        sw = max(target_w, int(round(vw * scale)))
        sh = max(target_h, int(round(vh * scale)))
        resized = cv2.resize(rgb, (sw, sh), interpolation=cv2.INTER_LINEAR)
        x0 = max(0, (sw - target_w) // 2)
        y0 = max(0, (sh - target_h) // 2)
        bg_crop = resized[y0 : y0 + target_h, x0 : x0 + target_w]
        dw = max(4, target_w // 4)
        dh = max(4, target_h // 4)
        small = cv2.resize(bg_crop, (dw, dh), interpolation=cv2.INTER_AREA)
        sigma = max(1.5, float(self._bg_blur_sigma) / 5.0)
        blurred = cv2.GaussianBlur(small, (0, 0), sigmaX=sigma, sigmaY=sigma)
        up = np.ascontiguousarray(cv2.resize(blurred, (target_w, target_h), interpolation=cv2.INTER_LINEAR))
        qout = QImage(up.data, target_w, target_h, target_w * 3, QImage.Format.Format_RGB888).copy()
        self._blur_pixmap = QPixmap.fromImage(qout)
        self._blur_cache_key = key
        return self._blur_pixmap

    def _hit_test_crop(self, pos: QPointF, crect: QRectF) -> int:
        if crect.isEmpty():
            return self.HANDLE_NONE
        m = 10.0
        l, r, t, b = crect.left(), crect.right(), crect.top(), crect.bottom()
        x, y = pos.x(), pos.y()
        near_l = abs(x - l) <= m
        near_r = abs(x - r) <= m
        near_t = abs(y - t) <= m
        near_b = abs(y - b) <= m
        in_x = (l - m) <= x <= (r + m)
        in_y = (t - m) <= y <= (b + m)

        if near_l and near_t:
            return self.HANDLE_TL
        if near_r and near_t:
            return self.HANDLE_TR
        if near_l and near_b:
            return self.HANDLE_BL
        if near_r and near_b:
            return self.HANDLE_BR
        if near_l and in_y:
            return self.HANDLE_L
        if near_r and in_y:
            return self.HANDLE_R
        if near_t and in_x:
            return self.HANDLE_T
        if near_b and in_x:
            return self.HANDLE_B
        if crect.contains(pos):
            return self.HANDLE_MOVE
        return self.HANDLE_NONE

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(self._backdrop_hex))

        if self._pixmap is None or self._video_w <= 0 or self._video_h <= 0:
            painter.setPen(QColor("#5C6484"))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "拖入视频文件，或点击右上角「打开视频...」",
            )
            return

        ws = self._compute_workspace()
        vrect = self._video_widget_rect(ws)
        crect = self._crop_widget_rect(ws)
        is_oob = self._crop.is_out_of_bounds(self._video_w, self._video_h)

        if not self._crop_editing:
            # Clean gallery preview: clip strictly to crect
            painter.save()
            painter.setClipRect(crect)
            if is_oob:
                if self._bg_mode == "solid":
                    painter.fillRect(crect, QColor(self._bg_color))
                else:
                    blur_pix = self._get_blurred_bg_pixmap(max(4, int(crect.width())), max(4, int(crect.height())))
                    if blur_pix is not None:
                        painter.drawPixmap(crect.toRect(), blur_pix)
            painter.drawPixmap(vrect.toRect(), self._pixmap)
            painter.restore()
            # Subtle hairline card border
            painter.setPen(QPen(QColor("#A4C6E9"), 1))
            painter.drawRect(crect)
            return

        # Interactive crop-editing mode
        if is_oob:
            if self._bg_mode == "solid":
                painter.fillRect(crect, QColor(self._bg_color))
            else:
                blur_pix = self._get_blurred_bg_pixmap(max(4, int(crect.width())), max(4, int(crect.height())))
                if blur_pix is not None:
                    painter.drawPixmap(crect.toRect(), blur_pix)

        painter.drawPixmap(vrect.toRect(), self._pixmap)

        mask_color = QColor(20, 24, 38, 130)
        inter = vrect.intersected(crect)
        if not inter.isEmpty():
            if inter.top() > vrect.top():
                painter.fillRect(QRectF(vrect.left(), vrect.top(), vrect.width(), inter.top() - vrect.top()), mask_color)
            if inter.bottom() < vrect.bottom():
                painter.fillRect(QRectF(vrect.left(), inter.bottom(), vrect.width(), vrect.bottom() - inter.bottom()), mask_color)
            if inter.left() > vrect.left():
                painter.fillRect(QRectF(vrect.left(), inter.top(), inter.left() - vrect.left(), inter.height()), mask_color)
            if inter.right() < vrect.right():
                painter.fillRect(QRectF(inter.right(), inter.top(), vrect.right() - inter.right(), inter.height()), mask_color)

        if is_oob:
            painter.setPen(QPen(QColor(254, 253, 249, 170), 1.5, Qt.PenStyle.DashLine))
            painter.drawRect(vrect)

        painter.setPen(QPen(QColor(254, 253, 249, 110), 1, Qt.PenStyle.DashLine))
        for i in (1, 2):
            gx = crect.left() + crect.width() * i / 3.0
            gy = crect.top() + crect.height() * i / 3.0
            painter.drawLine(QPointF(gx, crect.top()), QPointF(gx, crect.bottom()))
            painter.drawLine(QPointF(crect.left(), gy), QPointF(crect.right(), gy))

        # Periwinkle Indigo (#838BCC) crop border + handles
        painter.setPen(QPen(QColor("#838BCC"), 2.5))
        painter.drawRect(crect)
        painter.setBrush(QColor("#FEFDF9"))
        painter.setPen(QPen(QColor("#838BCC"), 2))
        hs = 9.0
        pts = [
            crect.topLeft(),
            crect.topRight(),
            crect.bottomLeft(),
            crect.bottomRight(),
            QPointF(crect.center().x(), crect.top()),
            QPointF(crect.center().x(), crect.bottom()),
            QPointF(crect.left(), crect.center().y()),
            QPointF(crect.right(), crect.center().y()),
        ]
        for pt in pts:
            painter.drawRoundedRect(QRectF(pt.x() - hs / 2, pt.y() - hs / 2, hs, hs), 2, 2)

        tag = " (外扩)" if is_oob else ""
        badge_text = f"{self._crop.w} × {self._crop.h}{tag}"
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(131, 139, 204, 215))
        painter.drawRoundedRect(QRectF(crect.left() + 6, crect.top() + 6, 136, 22), 5, 5)
        painter.setPen(QColor("#FEFDF9"))
        painter.drawText(
            QRectF(crect.left() + 10, crect.top() + 6, 130, 22),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            badge_text,
        )

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._video_w <= 0 or self._video_h <= 0:
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        step = 1.15 if delta > 0 else (1.0 / 1.15)
        self.set_zoom(self._zoom * step, anchor_pos=event.position())
        event.accept()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.contextMenuRequested.emit(event.globalPos())
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._video_w <= 0 or self._video_h <= 0:
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            self._drag_mode = self.HANDLE_PAN
            self._drag_start_pos = event.position()
            self._drag_start_pan = QPointF(self._pan)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        ws = self._compute_workspace()
        if self._crop_editing:
            hit = self._hit_test_crop(event.position(), self._crop_widget_rect(ws))
            if hit != self.HANDLE_NONE:
                self._drag_mode = hit
                self._drag_start_pos = event.position()
                self._drag_start_crop = self._crop
                self._drag_workspace = ws
                return

        # Left-drag anywhere (when crop editing off, or outside crop box) pans the canvas
        self._drag_mode = self.HANDLE_PAN
        self._drag_start_pos = event.position()
        self._drag_start_pan = QPointF(self._pan)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._video_w <= 0 or self._video_h <= 0:
            return
        ws = self._compute_workspace()
        scale = ws[2]

        if self._drag_mode == self.HANDLE_NONE:
            if self._crop_editing:
                mode = self._hit_test_crop(event.position(), self._crop_widget_rect(ws))
                cursors = {
                    self.HANDLE_TL: Qt.CursorShape.SizeFDiagCursor,
                    self.HANDLE_BR: Qt.CursorShape.SizeFDiagCursor,
                    self.HANDLE_TR: Qt.CursorShape.SizeBDiagCursor,
                    self.HANDLE_BL: Qt.CursorShape.SizeBDiagCursor,
                    self.HANDLE_L: Qt.CursorShape.SizeHorCursor,
                    self.HANDLE_R: Qt.CursorShape.SizeHorCursor,
                    self.HANDLE_T: Qt.CursorShape.SizeVerCursor,
                    self.HANDLE_B: Qt.CursorShape.SizeVerCursor,
                    self.HANDLE_MOVE: Qt.CursorShape.SizeAllCursor,
                }
                self.setCursor(cursors.get(mode, Qt.CursorShape.OpenHandCursor))
            else:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            return

        if self._drag_mode == self.HANDLE_PAN:
            delta = event.position() - self._drag_start_pos
            self._pan = self._drag_start_pan + delta
            self.update()
            return

        if scale <= 0:
            return
        dx_vid = (event.position().x() - self._drag_start_pos.x()) / scale
        dy_vid = (event.position().y() - self._drag_start_pos.y()) / scale
        c = self._drag_start_crop

        if self._drag_mode == self.HANDLE_MOVE:
            self.set_crop(CropRect(int(round(c.x + dx_vid)), int(round(c.y + dy_vid)), c.w, c.h), emit=True)
            return

        x1, y1, x2, y2 = float(c.x), float(c.y), float(c.x + c.w), float(c.y + c.h)
        if self._drag_mode in (self.HANDLE_TL, self.HANDLE_L, self.HANDLE_BL):
            x1 = min(x2 - 64.0, x1 + dx_vid)
        if self._drag_mode in (self.HANDLE_TR, self.HANDLE_R, self.HANDLE_BR):
            x2 = max(x1 + 64.0, x2 + dx_vid)
        if self._drag_mode in (self.HANDLE_TL, self.HANDLE_T, self.HANDLE_TR):
            y1 = min(y2 - 64.0, y1 + dy_vid)
        if self._drag_mode in (self.HANDLE_BL, self.HANDLE_B, self.HANDLE_BR):
            y2 = max(y1 + 64.0, y2 + dy_vid)

        nw = x2 - x1
        nh = y2 - y1
        if self._aspect_ratio is not None:
            r = self._aspect_ratio
            if nw / nh > r:
                nw = nh * r
            else:
                nh = nw / r
            if self._drag_mode in (self.HANDLE_TL, self.HANDLE_L, self.HANDLE_BL):
                x1 = x2 - nw
            if self._drag_mode in (self.HANDLE_TL, self.HANDLE_T, self.HANDLE_TR):
                y1 = y2 - nh

        self.set_crop(CropRect(int(round(x1)), int(round(y1)), int(round(nw)), int(round(nh))), emit=True)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_mode = self.HANDLE_NONE
        self._drag_workspace = None
        self.setCursor(Qt.CursorShape.OpenHandCursor if self._video_w > 0 else Qt.CursorShape.ArrowCursor)
        self.update()
