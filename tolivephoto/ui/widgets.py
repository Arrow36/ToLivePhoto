"""Custom PySide6 widgets: overflow-proof ComboBox, ElidedLabel, SegmentedBar, ChipButton, StyledCheckBox."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QWidget,
)


class NoWheelComboBox(QComboBox):
    """Overflow-proof ComboBox ignoring wheel scroll, with light Fusion popup & crisp chevron arrow."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        view = QListView(self)
        view.setTextElideMode(Qt.TextElideMode.ElideRight)
        pal = view.palette()
        pal.setColor(QPalette.ColorRole.Base, QColor("#FEFDF9"))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#F4F6FC"))
        pal.setColor(QPalette.ColorRole.Text, QColor("#25293A"))
        pal.setColor(QPalette.ColorRole.Highlight, QColor("#EBE8FB"))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#25293A"))
        view.setPalette(pal)
        self.setView(view)
        self.currentTextChanged.connect(self.setToolTip)

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self.width() - 13.0
        cy = self.height() / 2.0 + 1.0
        color = QColor("#7D85CC") if self.isEnabled() else QColor("#9DA5C0")
        p.setPen(
            QPen(
                color,
                1.8,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        p.drawLine(QPointF(cx - 4.0, cy - 2.0), QPointF(cx, cy + 2.5))
        p.drawLine(QPointF(cx, cy + 2.5), QPointF(cx + 4.0, cy - 2.0))


class ElidedLabel(QLabel):
    """Single-line label that elides long dynamic text without inflating container width."""

    def __init__(
        self,
        text: str = "",
        elide_mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self._full_text = text
        self._elide_mode = elide_mode
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setToolTip(text)

    def setText(self, text: str) -> None:  # type: ignore[override]
        self._full_text = text
        self.setToolTip(text)
        super().setText(text)
        self.update()

    def fullText(self) -> str:
        return self._full_text

    def sizeHint(self) -> QSize:
        sh = super().sizeHint()
        return QSize(min(sh.width(), 160), sh.height())

    def minimumSizeHint(self) -> QSize:
        sh = super().minimumSizeHint()
        return QSize(40, sh.height())

    def paintEvent(self, event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(self.foregroundRole()))
        m = self.contentsMargins()
        avail = max(10, self.width() - m.left() - m.right() - 4)
        elided = self.fontMetrics().elidedText(self._full_text, self._elide_mode, avail)
        rect = self.rect().adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        p.drawText(rect, int(self.alignment() | Qt.AlignmentFlag.AlignVCenter), elided)


class SegmentedBar(QFrame):
    """Recessed pill track hosting mutually exclusive segmented buttons."""

    selectionChanged = Signal(object)

    def __init__(
        self,
        items: list[tuple[str, object]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("segmentedBar")
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[tuple[object, QPushButton]] = []

        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(3)

        for label, key in items:
            btn = QPushButton(label, self)
            btn.setObjectName("segBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._group.addButton(btn)
            btn.clicked.connect(lambda _=False, k=key: self.selectionChanged.emit(k))
            lay.addWidget(btn, 1)
            self._buttons.append((key, btn))

        if self._buttons:
            self._buttons[0][1].setChecked(True)

    def setCurrentKey(self, key: object) -> None:
        for k, btn in self._buttons:
            if k == key:
                btn.setChecked(True)
                return

    def currentKey(self) -> object:
        for k, btn in self._buttons:
            if btn.isChecked():
                return k
        return self._buttons[0][0] if self._buttons else None

    def buttonForKey(self, key: object) -> QPushButton | None:
        for k, btn in self._buttons:
            if k == key:
                return btn
        return None


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


class ChipButton(QPushButton):
    """Compact checkable pill button."""

    def __init__(self, text: str, parent: QWidget | None = None, checkable: bool = True) -> None:
        super().__init__(text, parent)
        self.setCheckable(checkable)
        self.setObjectName("chipBtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)


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
    """High-contrast pastel-themed checkbox with explicit #7D85CC fill and white checkmark."""

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
            p.setPen(QPen(QColor("#7D85CC"), 1.5))
            p.setBrush(QColor("#7D85CC"))
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

        p.setPen(QColor("#25293A" if self.isEnabled() else "#7C85A5"))
        p.drawText(
            x + box_sz + 8,
            0,
            self.width() - (x + box_sz + 10),
            self.height(),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.text(),
        )
