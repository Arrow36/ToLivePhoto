"""Custom PySide6 widgets: no-wheel inputs, 4-digit DCF spinbox, OptionPillButton, StyledCheckBox."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QWidget,
)


class NoWheelComboBox(QComboBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


class FourDigitSpinBox(NoWheelSpinBox):
    """Zero-padded 0001-9999 camera DCF number spinbox."""

    def textFromValue(self, val: int) -> str:
        return f"{val:04d}"


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


class NoWheelSlider(QSlider):
    def __init__(self, orientation: Qt.Orientation, parent: QWidget | None = None) -> None:
        super().__init__(orientation, parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


class OptionPillButton(QPushButton):
    """Selectable option button with unmistakable checked indicator (● vs ○)."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = text
        self.setCheckable(True)
        self.setObjectName("modeOptionBtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(self._sync_prefix)
        self._sync_prefix(False)

    def _sync_prefix(self, checked: bool) -> None:
        prefix = "● " if checked else "○ "
        super().setText(prefix + self._label)


class StyledCheckBox(QCheckBox):
    """High-contrast pastel-themed checkbox with explicit #838BCC fill and white checkmark."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box_sz = 16
        y = (self.height() - box_sz) // 2
        x = 2
        if self.isChecked():
            p.setPen(QPen(QColor("#838BCC"), 1.5))
            p.setBrush(QColor("#838BCC"))
            p.drawRoundedRect(x, y, box_sz, box_sz, 4, 4)
            p.setPen(
                QPen(
                    QColor("#FEFDF9"),
                    2.0,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            p.drawLine(x + 3, y + 8, x + 7, y + 12)
            p.drawLine(x + 7, y + 12, x + 13, y + 4)
        else:
            p.setPen(QPen(QColor("#A4C6E9"), 1.5))
            p.setBrush(QColor("#FEFDF9"))
            p.drawRoundedRect(x, y, box_sz, box_sz, 4, 4)

        p.setPen(QColor("#2D3142" if self.isEnabled() else "#8891B0"))
        p.drawText(
            x + box_sz + 8,
            0,
            self.width() - (x + box_sz + 10),
            self.height(),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.text(),
        )
