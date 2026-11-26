"""Programmatic Live Photo concentric-rings app icon rendered in ToLivePhoto palette."""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QRadialGradient


def create_app_icon() -> QIcon:
    size = 128
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    grad = QRadialGradient(QPointF(40, 36), 96)
    grad.setColorAt(0.0, QColor("#C7F3F6"))
    grad.setColorAt(0.55, QColor("#A4C6E9"))
    grad.setColorAt(1.0, QColor("#838BCC"))

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(6, 6, size - 12, size - 12, 28, 28)

    center = QPointF(size / 2, size / 2)
    # Outer dashed Live Photo ring
    pen_dash = QPen(QColor("#FEFDF9"), 5.5, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen_dash)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(center, 40, 40)

    # Middle solid ring
    p.setPen(QPen(QColor("#FADCFE"), 4.5))
    p.drawEllipse(center, 24, 24)

    # Inner core dot
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#FEFDF9"))
    p.drawEllipse(center, 9, 9)

    p.end()
    return QIcon(pm)
