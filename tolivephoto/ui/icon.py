"""Application icon loader for ToLivePhoto using bundled high-DPI PNG/ICO assets."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap, QRadialGradient

ICONS_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"
ICON_PNG_PATH = ICONS_DIR / "app_icon.png"
ICON_ICO_PATH = ICONS_DIR / "app_icon.ico"


def create_app_icon() -> QIcon:
    """Return the multi-resolution ToLivePhoto QIcon."""
    if ICON_PNG_PATH.is_file():
        img = QImage(str(ICON_PNG_PATH))
        if not img.isNull():
            icon = QIcon()
            for sz in (16, 24, 32, 48, 64, 128, 256):
                scaled = img.scaled(
                    sz,
                    sz,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                icon.addPixmap(QPixmap.fromImage(scaled))
            return icon

    if ICON_ICO_PATH.is_file():
        icon = QIcon(str(ICON_ICO_PATH))
        if not icon.isNull():
            return icon

    # Minimal fallback if assets are missing
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
    p.end()
    return QIcon(pm)
