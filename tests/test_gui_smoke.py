"""Offscreen GUI smoke test for ToLivePhoto v2.2 unified Simple/Advanced studio UI, thumbnail queue, and zero-dropdowns."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox

from tolivephoto.core.models import CropRect
from tolivephoto.ui.main_window import FPS_PRESET_VALUES, LONG_EDGE_PRESETS, MainWindow
from tolivephoto.ui.theme import ensure_bytedance_font


def test_tolivephoto_gui_v22_offscreen(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    font_family = ensure_bytedance_font(app)
    assert font_family

    sample = Path.home() / "Desktop" / "CSI_7607.mp4"
    win = MainWindow(initial_video=sample if sample.is_file() else None)
    win.show()
    app.processEvents()

    # 1. Only output-directory USB selector is a QComboBox (all other selectors are buttons)
    combos = win.findChildren(QComboBox)
    assert combos == [win.combo_usb]

    # 2. Default Simple Mode vs Advanced Mode switching
    assert win.simple_container.isVisible()
    assert not win.advanced_container.isVisible()
    win.set_ui_mode("advanced")
    app.processEvents()
    assert not win.simple_container.isVisible()
    assert win.advanced_container.isVisible()

    # 3. Crop button lives inside the unified studio_card
    assert win.studio_card.isAncestorOf(win.btn_crop_toggle)

    # 4. Zero-overflow layout assertion on unified studio card
    assert win.studio_card.minimumSizeHint().width() <= 490

    # 5. Defaults check
    assert "ToLivePhoto" in win.windowTitle()
    assert win.rb_keep.isChecked()
    assert win.spin_custom_fps.value() == 30.0
    assert win.spin_custom_edge.value() == 0
    assert list(LONG_EDGE_PRESETS) == [0, 2160, 1440, 1080, 720]
    assert list(FPS_PRESET_VALUES) == [24, 25, 30, 60]

    # 6. Crop box hidden by default until toggled
    assert not win.btn_crop_toggle.isChecked()
    assert not win.preview.is_crop_editing()
    win.btn_crop_toggle.setChecked(True)
    assert win.preview.is_crop_editing()

    # 7. Flat background buttons & bitrate slider
    assert win.btn_bg_blur.isChecked()
    win.slider_bitrate.setValue(32)
    assert "32 Mbps" in win.lbl_bitrate_val.text()

    if sample.is_file():
        # 8. Bottom video info bar shows full directory path & filename
        assert str(sample.parent) in win.lbl_full_dir_info.text()
        assert sample.name in win.lbl_full_dir_info.text()

        # 9. Summary uses X秒Y帧 with no decimal point
        assert "秒" in win.lbl_summary.text() and "帧" in win.lbl_summary.text()
        assert "预计" in win.lbl_est_size.text()

        # 10. Simple Mode FPS presets update job cleanly
        win.set_ui_mode("simple")
        win._on_simple_fps_changed("raw_30")
        assert win._build_job().fps_mode == "raw_retime"
        assert win._build_job().custom_fps == 30.0
        win._on_simple_fps_changed("rife_fast")
        job_fast = win._build_job()
        assert job_fast.fps_mode in ("rife_interpolate", "ffmpeg_minterpolate")
        assert job_fast.custom_fps == 30.0
        win._on_simple_fps_changed("keep_original")
        win.set_ui_mode("advanced")

        # 11. Colon-free dual ratio input, alignment, and orientation flip
        win.edit_ratio_w.setText("16")
        win.edit_ratio_h.setText("9")
        win._apply_manual_ratio()
        c_16_9 = win.preview.get_crop()
        assert abs((c_16_9.w / c_16_9.h) - (16.0 / 9.0)) < 0.02

        win._on_align_clicked("top")
        assert win.preview.get_crop().y == 0
        win._on_align_clicked("center")
        c_ctr = win.preview.get_crop()
        assert abs(c_ctr.y - (3240 - c_ctr.h) // 2) <= 2

        win._on_orientation_changed("portrait")
        win._on_aspect_button_clicked("3:4")
        c_3_4 = win.preview.get_crop()
        assert c_3_4.w < c_3_4.h
        win._on_orientation_changed("landscape")
        c_4_3 = win.preview.get_crop()
        assert c_4_3.w > c_4_3.h

        # 12. Hold-to-compare & Polaroid & Double-click zoom
        win.preview.set_compare_original(True)
        assert win.preview.is_comparing_original()
        win.preview.set_compare_original(False)

        win._apply_polaroid_preset("mini")
        pad = win.preview.get_padding()
        assert pad.bottom > pad.top > 0
        win._reset_crop()
        assert win.preview.get_padding().is_zero()

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

        win.preview.set_crop(CropRect(-100, 0, 2360, 3240), emit=True)
        assert "外扩" in win.lbl_crop_res.text()

    # 13. USB NoWheelComboBox ignores wheel events
    ev = QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        win.combo_usb.rect().topLeft(),
        win.combo_usb.rect().topLeft(),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(win.combo_usb, ev)

    win.close()
    app.processEvents()
