"""Offscreen GUI smoke test for ToLivePhoto v2.1 overflow-free Studio UI, stepper, queue bar, & canvas interactions."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
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

    # 2. Zero-overflow layout assertion: every card must fit cleanly inside 500px+ inspector
    for card in (win.c1, win.c2, win.c3, win.c4):
        assert card.minimumSizeHint().width() <= 490

    # 3. Smart Queue Bar hidden when <= 1 video is loaded
    assert not win.queue_bar.isVisible()

    # 4. Stepper navigation & Expand-All toggle
    assert win.c1.isVisible() and not win.c2.isVisible()
    win._set_active_step(1)
    assert win.c2.isVisible() and not win.c1.isVisible()
    win.btn_expand_all.setChecked(True)
    app.processEvents()
    assert all(c.isVisible() for c in (win.c1, win.c2, win.c3, win.c4))
    win.btn_expand_all.setChecked(False)
    win._set_active_step(0)

    # 5. Crop box hidden by default until toggled
    assert not win.btn_crop_toggle.isChecked()
    assert not win.preview.is_crop_editing()
    win.btn_crop_toggle.setChecked(True)
    assert win.preview.is_crop_editing()

    # 6. NoWheelComboBox ignores wheel events
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

    # 7. Flat background buttons & bitrate slider
    assert win.btn_bg_blur.isChecked()
    win.slider_bitrate.setValue(32)
    assert "32 Mbps" in win.lbl_bitrate_val.text()

    if sample.is_file():
        # 8. Summary uses X秒Y帧 with no decimal point
        assert "秒" in win.lbl_summary.text() and "帧" in win.lbl_summary.text()
        assert "预计" in win.lbl_est_size.text()

        # 9. Hold-to-compare raw frame toggle
        win.preview.set_compare_original(True)
        assert win.preview.is_comparing_original()
        win.preview.set_compare_original(False)
        assert not win.preview.is_comparing_original()

        # 10. Polaroid preset applies asymmetric padding and white background
        win._apply_polaroid_preset("mini")
        pad = win.preview.get_padding()
        assert pad.bottom > pad.top > 0
        assert "Instax Mini" in win.lbl_crop_res.text()
        win._reset_crop()
        assert win.preview.get_padding().is_zero()

        # 11. Wheel zoom & double-click zoom toggle
        win.preview.set_zoom(1.5)
        assert "150%" in win.btn_zoom_reset.text()
        win.preview.reset_view()
        assert abs(win.preview.get_zoom() - 1.0) < 1e-3

        dbl = QMouseEvent(
            QEvent.Type.MouseButtonDblClick,
            QPointF(win.preview.width() / 2.0, win.preview.height() / 2.0),
            QPointF(win.preview.width() / 2.0, win.preview.height() / 2.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(win.preview, dbl)
        assert abs(win.preview.get_zoom() - 2.0) < 1e-3
        QApplication.sendEvent(win.preview, dbl)
        assert abs(win.preview.get_zoom() - 1.0) < 1e-3

        # 12. Out-of-bounds crop works
        win.preview.set_crop(CropRect(-100, 0, 2360, 3240), emit=True)
        assert "外扩" in win.lbl_crop_res.text()

    win.close()
    app.processEvents()
