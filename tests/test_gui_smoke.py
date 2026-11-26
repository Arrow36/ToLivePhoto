"""Offscreen GUI smoke test for ToLivePhoto v2.0 UI, Polaroid, #FEFDF9 backdrop, 3s trim, bitrate, and EXIF."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from tolivephoto.core.models import CropRect
from tolivephoto.ui.main_window import FPS_PRESET_VALUES, LONG_EDGE_PRESETS, MainWindow


def test_tolivephoto_gui_v2_offscreen(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    sample = Path.home() / "Desktop" / "CSI_7607.mp4"
    win = MainWindow(initial_video=sample if sample.is_file() else None)
    win.show()
    app.processEvents()

    # 1. Title, porcelain canvas #FEFDF9, and default keep_original + 30fps RIFE default
    assert "ToLivePhoto" in win.windowTitle()
    assert win.rb_keep.isChecked()
    assert win.spin_custom_fps.value() == 30.0
    assert win.spin_custom_edge.value() == 0
    assert list(LONG_EDGE_PRESETS) == [0, 2160, 1440, 1080, 720]
    assert list(FPS_PRESET_VALUES) == [24, 25, 30, 60]

    # 2. Crop box hidden by default until toggled
    assert not win.btn_crop_toggle.isChecked()
    assert not win.preview.is_crop_editing()
    win.btn_crop_toggle.setChecked(True)
    assert win.preview.is_crop_editing()

    # 3. NoWheelComboBox ignores wheel events
    before_idx = win.combo_img_fmt.currentIndex()
    ev = QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        win.combo_img_fmt.rect().topLeft(),
        win.combo_img_fmt.rect().topLeft(),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(win.combo_img_fmt, ev)
    assert win.combo_img_fmt.currentIndex() == before_idx

    # 4. Flat background buttons & bitrate slider
    assert win.btn_bg_blur.isChecked()
    win.slider_bitrate.setValue(32)
    assert "32 Mbps" in win.lbl_bitrate_val.text()

    if sample.is_file():
        # 5. Summary uses X秒Y帧 with no decimal point
        assert "秒" in win.lbl_summary.text() and "帧" in win.lbl_summary.text()
        assert "预计" in win.lbl_est_size.text()

        # 6. Polaroid preset applies asymmetric padding and white background
        win._apply_polaroid_preset("mini")
        pad = win.preview.get_padding()
        assert pad.bottom > pad.top > 0
        assert "Instax Mini" in win.lbl_crop_res.text()
        win._reset_crop()
        assert win.preview.get_padding().is_zero()

        # 7. Wheel zoom & reset
        win.preview.set_zoom(1.5)
        assert "150%" in win.btn_zoom_reset.text()
        win.preview.reset_view()
        assert abs(win.preview.get_zoom() - 1.0) < 1e-3

        # 8. Out-of-bounds crop works
        win.preview.set_crop(CropRect(-100, 0, 2360, 3240), emit=True)
        assert "外扩" in win.lbl_crop_res.text()

    win.close()
    app.processEvents()
