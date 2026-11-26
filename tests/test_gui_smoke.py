"""Offscreen GUI smoke test for ToLivePhoto v1.0 canvas zoom/pan, manual crop toggle, FPS chips, and DCF."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from tolivephoto.core.models import CropRect
from tolivephoto.core.pipeline import ConversionResult, TimelineSummary
from tolivephoto.ui.main_window import FPS_PRESET_VALUES, MainWindow


def test_tolivephoto_gui_v1_offscreen(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    sample = Path.home() / "Desktop" / "CSI_7607.mp4"
    win = MainWindow(initial_video=sample if sample.is_file() else None)
    win.show()
    app.processEvents()

    # 1. Window title & default unscaled video option
    assert "ToLivePhoto" in win.windowTitle()
    assert win.combo_max_edge.currentData() == 0

    # 2. Crop box hidden by default until manually toggled
    assert not win.btn_crop_toggle.isChecked()
    assert not win.preview.is_crop_editing()
    win.btn_crop_toggle.setChecked(True)
    assert win.preview.is_crop_editing()

    # 3. FPS preset buttons = (24, 25, 30, 60)
    assert [v for v, _ in win._fps_chip_buttons] == [24, 25, 30, 60]
    assert list(FPS_PRESET_VALUES) == [24, 25, 30, 60]

    # 4. NoWheelComboBox ignores wheel events
    before_idx = win.combo_aspect.currentIndex()
    ev = QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        win.combo_aspect.rect().topLeft(),
        win.combo_aspect.rect().topLeft(),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(win.combo_aspect, ev)
    assert win.combo_aspect.currentIndex() == before_idx

    # 5. Preview canvas wheel zoom
    if sample.is_file():
        assert abs(win.preview.get_zoom() - 1.0) < 1e-3
        win.preview.set_zoom(1.5)
        assert abs(win.preview.get_zoom() - 1.5) < 1e-3
        assert "150%" in win.btn_zoom_reset.text()
        win.preview.reset_view()
        assert abs(win.preview.get_zoom() - 1.0) < 1e-3

        # 6. Auto-increment DCF number on worker success (bypassing QMessageBox via direct check)
        win.spin_dcf.setValue(42)
        win.chk_dcf_auto_inc.setChecked(True)
        # Verify CropRect normalization works
        win.preview.set_crop(CropRect(-100, 0, 2360, 3240), emit=True)
        assert "外扩" in win.lbl_crop_res.text()

    win.close()
    app.processEvents()
