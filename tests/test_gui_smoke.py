"""Offscreen GUI smoke test for ToLivePhoto v2.3 streamlined framing, dual info bar, blank EXIF, & fixed simple bitrate."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QWidget

from tolivephoto.core.models import IPHONE_LIVE_BITRATE_MBPS, CropRect
from tolivephoto.ui.main_window import FPS_PRESET_VALUES, LONG_EDGE_PRESETS, MainWindow
from tolivephoto.ui.theme import ensure_bytedance_font


def test_tolivephoto_gui_v23_offscreen(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    font_family = ensure_bytedance_font(app)
    assert font_family

    sample = Path.home() / "Desktop" / "CSI_7607.mp4"
    win = MainWindow(initial_video=sample if sample.is_file() else None)
    win.show()
    app.processEvents()

    # 1. Only output-directory USB selector is a QComboBox; no top brandHeader; splitter gap >= 12px
    assert win.findChildren(QComboBox) == [win.combo_usb]
    assert win.findChild(QWidget, "brandHeader") is None
    assert win.main_splitter.handleWidth() >= 12
    assert win.queue_bar.y() < win.video_info_bar.y()

    # 2. No locate button; dual info bar present; blank default EXIF
    assert not hasattr(win, "btn_locate_source")
    assert "空白" in win.lbl_exif_summary.text()

    # 3. Default Simple Mode uses fixed iPhone bitrate (8 Mbps) & _2live naming (visible preview)
    assert win.simple_container.isVisible()
    assert not win.advanced_container.isVisible()
    if sample.is_file():
        job_simple = win._build_job()
        assert job_simple.video_bitrate_mbps == IPHONE_LIVE_BITRATE_MBPS
        assert "_2live" in win.lbl_out_file_info.text()
        assert "_2live" in win.lbl_folder_dest_preview.text()
        assert win.lbl_folder_dest_preview.isVisible()
        assert str(sample.parent) in win.lbl_full_dir_info.text()
        assert sample.name in win.lbl_full_dir_info.text()
        assert "输出规格:" in win.lbl_out_spec_info.text()

    # 4. Switch to Advanced Mode & verify zero overflow
    win.set_ui_mode("advanced")
    app.processEvents()
    assert not win.simple_container.isVisible()
    assert win.advanced_container.isVisible()
    assert win.studio_card.isAncestorOf(win.btn_crop_toggle)
    assert win.studio_card.minimumSizeHint().width() <= 490

    # 5. Key Photo: selecting video frame hides external image container
    win.seg_key_source.setCurrentKey("video_frame")
    win._on_key_source_changed()
    assert win.video_key_row_widget.isVisible()
    assert not win.ext_key_container.isVisible()
    win.seg_key_source.setCurrentKey("external_image")
    win._on_key_source_changed()
    assert not win.video_key_row_widget.isVisible()
    assert win.ext_key_container.isVisible()
    win.seg_key_source.setCurrentKey("video_frame")
    win._on_key_source_changed()

    # 6. Defaults check
    assert "ToLivePhoto" in win.windowTitle()
    assert win.rb_keep.isChecked()
    assert win.spin_custom_fps.value() == 30.0
    assert win.spin_custom_edge.value() == 0
    assert list(LONG_EDGE_PRESETS) == [0, 2160, 1440, 1080, 720]
    assert list(FPS_PRESET_VALUES) == [24, 25, 30, 60]

    # 7. Unified 3-way framing: crop mode turns on crop_editing automatically
    assert not win.preview.is_crop_editing()
    win._on_framing_mode_changed("crop")
    assert win.preview.is_crop_editing()
    assert win.crop_subpanel.isVisible()

    if sample.is_file():
        # 8. Live scaled resolution spec beside custom px
        win.spin_custom_edge.setValue(1440)
        assert "960×1440" in win.lbl_scaled_res_spec.text()
        win.spin_custom_edge.setValue(0)
        assert "2160×3240" in win.lbl_scaled_res_spec.text()

        # 9. Colon-free ratio, alignment, and unified orientation flip
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
        assert win.preview.is_crop_editing()

        # 10. Custom ratio (9:15) persists across inside <-> pad
        win.edit_ratio_w.setText("9")
        win.edit_ratio_h.setText("15")
        win._apply_manual_ratio()
        c_9_15_in = win.preview.get_crop()
        assert abs((c_9_15_in.w / c_9_15_in.h) - (9.0 / 15.0)) < 0.02
        win.seg_fit_mode.setCurrentKey("pad")
        win._reapply_current_aspect("pad")
        c_9_15_pad = win.preview.get_crop()
        assert abs((c_9_15_pad.w / c_9_15_pad.h) - (9.0 / 15.0)) < 0.02
        win.seg_fit_mode.setCurrentKey("inside")
        win._reapply_current_aspect("inside")

        # 11. Click outside video/sheet hides crop box
        assert win.preview.is_crop_editing()
        click_out = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(2.0, 2.0),
            QPointF(2.0, 2.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(win.preview, click_out)
        assert not win.preview.is_crop_editing()
        assert win.btn_crop_toggle.isChecked()

        # 12. Composable Crop + Polaroid & ratio labels
        assert "3:4" in win._polaroid_buttons["mini"].text()
        assert "16:10" in win._polaroid_buttons["wide"].text()
        assert "custom_crop" in win._polaroid_buttons
        win._apply_polaroid_preset("custom_crop")
        pad_custom = win.preview.get_padding()
        assert pad_custom.bottom > pad_custom.top > 0
        c_after_custom_pol = win.preview.get_crop()
        assert abs((c_after_custom_pol.w / c_after_custom_pol.h) - (9.0 / 15.0)) < 0.02

        win._apply_polaroid_preset("mini")
        pad = win.preview.get_padding()
        assert pad.bottom > pad.top > 0
        assert win.bg_style_panel.isVisible()
        win._reset_crop()
        assert win.preview.get_padding().is_zero()
        assert not win.preview.is_crop_editing()

        # 13. Compressed window layout check
        win.resize(920, 760)
        app.processEvents()
        assert win.studio_card.minimumWidth() <= 440
        assert win.width() <= 960

        # 11. Double-click zoom toggle
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
