"""Main PySide6 desktop studio window for ToLivePhoto."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QPoint, QSettings, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QPixmap,
    QRegularExpressionValidator,
    QShortcut,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.models import ConversionJob, CropRect, VideoInfo, compute_timeline_summary
from ..core.pipeline import ConversionResult
from ..core.probe import probe_video
from ..core.tools import find_rife, has_nvenc, list_available_rife_models
from ..core.usb_export import extract_4digit_seed, list_usb_drives, normalize_dcf_prefix
from .crop_preview_widget import CropPreviewWidget
from .frame_provider import PreviewFrameProvider
from .icon import create_app_icon
from .range_slider import RangeTimelineSlider
from .theme import PALETTE, PASTEL_STYLESHEET
from .widgets import (
    FourDigitSpinBox,
    NoWheelComboBox,
    NoWheelDoubleSpinBox,
    NoWheelSlider,
    NoWheelSpinBox,
    OptionPillButton,
    StyledCheckBox,
)
from .worker import ConversionWorker, RifeDownloadWorker

ASPECT_PRESETS: list[tuple[str, float | None]] = [
    ("原始比例", None),
    ("自由裁切", -1.0),
    ("2:3 (相机竖屏)", 2.0 / 3.0),
    ("3:4 (手机竖屏)", 3.0 / 4.0),
    ("9:16 (全屏竖屏)", 9.0 / 16.0),
    ("1:1 (正方形)", 1.0),
    ("4:3 (横屏)", 4.0 / 3.0),
    ("16:9 (宽屏)", 16.0 / 9.0),
]

FPS_PRESET_VALUES = (24, 25, 30, 60)


def _make_section_card(badge_num: str, title: str) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("sectionCard")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(10, 8, 10, 8)
    lay.setSpacing(6)

    hdr = QHBoxLayout()
    hdr.setSpacing(6)
    b = QLabel(badge_num)
    b.setObjectName("sectionBadge")
    t = QLabel(title)
    t.setObjectName("sectionTitle")
    hdr.addWidget(b)
    hdr.addWidget(t)
    hdr.addStretch(1)
    lay.addLayout(hdr)
    return card, lay


class MainWindow(QMainWindow):
    def __init__(self, initial_video: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("ToLivePhoto — 视频转 iPhone 实况照片工作台")
        self.setWindowIcon(create_app_icon())
        self.resize(1460, 880)
        self.setAcceptDrops(True)
        self.setStyleSheet(PASTEL_STYLESHEET)

        self._settings = QSettings("Arrow36", "ToLivePhoto")
        self._video_info: VideoInfo | None = None
        self._frame_provider = PreviewFrameProvider(self)
        self._frame_provider.frameAvailable.connect(self._on_proxy_frame_decoded)

        self._current_frame = 0
        self._is_playing = False
        self._play_clock = QElapsedTimer()
        self._play_start_frame = 0
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(16)
        self._play_timer.timeout.connect(self._on_play_tick)

        self._external_key_path: Path | None = None
        self._last_result: ConversionResult | None = None
        self._worker: ConversionWorker | None = None
        self._rife_worker: RifeDownloadWorker | None = None
        self._syncing_ui = False
        self._bg_color_hex = str(self._settings.value("bg_color", "#FEFDF9"))
        self._backdrop_hex = str(self._settings.value("backdrop_hex", PALETTE["canvas_light"]))
        self._nvenc_available = has_nvenc()
        self._fps_chip_buttons: list[tuple[int, QPushButton]] = []

        self._build_ui()
        self._load_persisted_dcf_settings()
        self._bind_shortcuts()
        self._refresh_usb_drives()

        if initial_video is not None and initial_video.is_file():
            self.load_video(initial_video)
        else:
            default_sample = Path.home() / "Desktop" / "CSI_7607.mp4"
            if default_sample.is_file():
                self.load_video(default_sample)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        # ---- TOP BRAND HEADER ----
        header = QFrame()
        header.setObjectName("brandHeader")
        header.setFixedHeight(52)
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(12, 6, 12, 6)
        h_lay.setSpacing(10)

        lbl_icon = QLabel()
        lbl_icon.setPixmap(create_app_icon().pixmap(28, 28))
        lbl_brand = QLabel("ToLivePhoto")
        lbl_brand.setStyleSheet("font-size: 16px; font-weight: bold; color: #2D3142;")
        lbl_pill = QLabel("LIVE STUDIO")
        lbl_pill.setObjectName("livePill")

        self.lbl_video_info = QLabel("未加载视频 (支持直接拖入 MP4 / MOV)")
        self.lbl_video_info.setObjectName("metaCapsule")
        self.btn_open = QPushButton("打开视频...")
        self.btn_open.clicked.connect(self._on_open_video)

        h_lay.addWidget(lbl_icon)
        h_lay.addWidget(lbl_brand)
        h_lay.addWidget(lbl_pill)
        h_lay.addSpacing(6)
        h_lay.addWidget(self.lbl_video_info, 1)
        h_lay.addWidget(self.btn_open)
        outer.addWidget(header)

        # ---- MAIN BODY: LEFT STAGE + RIGHT INSPECTOR ----
        body = QHBoxLayout()
        body.setSpacing(10)
        outer.addLayout(body, 1)

        # Left Stage Card
        stage_card = QFrame()
        stage_card.setObjectName("stageCard")
        left_layout = QVBoxLayout(stage_card)
        left_layout.setContentsMargins(10, 8, 10, 8)
        left_layout.setSpacing(6)

        stage_top = QHBoxLayout()
        stage_top.setSpacing(6)
        self.btn_crop_toggle = QPushButton("✂ 裁切画面")
        self.btn_crop_toggle.setObjectName("cropToggleBtn")
        self.btn_crop_toggle.setCheckable(True)
        self.btn_crop_toggle.setChecked(False)
        self.btn_crop_toggle.toggled.connect(self._on_crop_toggle_changed)

        self.lbl_crop_res = QLabel("画布: -")
        self.lbl_crop_res.setObjectName("metaCapsule")
        self.lbl_canvas_hint = QLabel("滚轮缩放 · 左键拖拽平移 · 右键更多选项")
        self.lbl_canvas_hint.setStyleSheet(f"color: {PALETTE['ink_muted']}; font-size: 11px;")
        self.btn_zoom_reset = QPushButton("100% 重置视图")
        self.btn_zoom_reset.clicked.connect(lambda: self.preview.reset_view())

        stage_top.addWidget(self.btn_crop_toggle)
        stage_top.addWidget(self.lbl_crop_res)
        stage_top.addStretch(1)
        stage_top.addWidget(self.lbl_canvas_hint)
        stage_top.addWidget(self.btn_zoom_reset)
        left_layout.addLayout(stage_top)

        self.preview = CropPreviewWidget()
        self.preview.set_backdrop_color(self._backdrop_hex)
        self.preview.cropChanged.connect(self._on_crop_changed)
        self.preview.zoomChanged.connect(self._on_zoom_changed)
        self.preview.contextMenuRequested.connect(self._on_preview_context_menu)
        left_layout.addWidget(self.preview, 1)

        transport = QHBoxLayout()
        transport.setSpacing(5)
        self.btn_jump_in = QPushButton("⏮ 入点")
        self.btn_prev_frame = QPushButton("◀ -1帧")
        self.btn_play = QPushButton("▶ 播放")
        self.btn_next_frame = QPushButton("+1帧 ▶")
        self.btn_jump_out = QPushButton("出点 ⏭")
        self.btn_set_in = QPushButton("[ 设为起点")
        self.btn_set_out = QPushButton("设为终点 ]")
        self.btn_set_key = QPushButton("★ 设为主图 (K)")
        self.btn_set_key.setObjectName("keyStarBtn")

        self.btn_jump_in.clicked.connect(lambda: self._seek_frame(self.spin_start.value()))
        self.btn_prev_frame.clicked.connect(lambda: self._seek_frame(self._current_frame - 1))
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_next_frame.clicked.connect(lambda: self._seek_frame(self._current_frame + 1))
        self.btn_jump_out.clicked.connect(lambda: self._seek_frame(self.spin_end.value()))
        self.btn_set_in.clicked.connect(self._set_current_as_start)
        self.btn_set_out.clicked.connect(self._set_current_as_end)
        self.btn_set_key.clicked.connect(self._set_current_as_key)

        for b in (
            self.btn_jump_in,
            self.btn_prev_frame,
            self.btn_play,
            self.btn_next_frame,
            self.btn_jump_out,
            self.btn_set_in,
            self.btn_set_out,
            self.btn_set_key,
        ):
            transport.addWidget(b)
        left_layout.addLayout(transport)

        self.timeline = RangeTimelineSlider()
        self.timeline.rangeChanged.connect(self._on_slider_range_changed)
        self.timeline.currentFrameChanged.connect(self._seek_frame)
        left_layout.addWidget(self.timeline)

        self.lbl_playhead = QLabel("当前: 第 0 帧  |  当前设定下时刻: 0.00s")
        self.lbl_playhead.setStyleSheet(f"color: {PALETTE['ink_secondary']}; font-size: 11px;")
        left_layout.addWidget(self.lbl_playhead)

        body.addWidget(stage_card, 1)

        # ---- RIGHT INSPECTOR + PINNED FOOTER ----
        right_col = QWidget()
        right_col.setFixedWidth(550)
        right_col_lay = QVBoxLayout(right_col)
        right_col_lay.setContentsMargins(0, 0, 0, 0)
        right_col_lay.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        cards_wrap = QWidget()
        cards_lay = QVBoxLayout(cards_wrap)
        cards_lay.setContentsMargins(0, 0, 4, 0)
        cards_lay.setSpacing(7)
        scroll.setWidget(cards_wrap)
        right_col_lay.addWidget(scroll, 1)

        # Card 01: Range, Crop & Background, Audio
        c1, c1_lay = _make_section_card("01", "范围、裁切外扩与声音")
        row_range = QHBoxLayout()
        row_range.addWidget(QLabel("起点帧:"))
        self.spin_start = NoWheelSpinBox()
        self.spin_start.valueChanged.connect(self._on_range_spin_changed)
        row_range.addWidget(self.spin_start)
        row_range.addWidget(QLabel("终点帧:"))
        self.spin_end = NoWheelSpinBox()
        self.spin_end.valueChanged.connect(self._on_range_spin_changed)
        row_range.addWidget(self.spin_end)
        self.chk_mute = StyledCheckBox("静音")
        self.chk_mute.toggled.connect(self._update_summary)
        row_range.addWidget(self.chk_mute)
        self.combo_audio_retime = NoWheelComboBox()
        self.combo_audio_retime.addItem("音频同步变速", "speed_sync")
        self.combo_audio_retime.addItem("原速截断淡出", "trim_fade")
        row_range.addWidget(self.combo_audio_retime, 1)
        c1_lay.addLayout(row_range)

        row_crop = QHBoxLayout()
        row_crop.addWidget(QLabel("比例:"))
        self.combo_aspect = NoWheelComboBox()
        for label, _ in ASPECT_PRESETS:
            self.combo_aspect.addItem(label)
        self.combo_aspect.currentIndexChanged.connect(lambda _idx: self._apply_aspect_action("inside"))
        row_crop.addWidget(self.combo_aspect, 1)
        self.btn_fit_inside = QPushButton("内裁填满")
        self.btn_fit_inside.clicked.connect(lambda: self._apply_aspect_action("inside"))
        self.btn_fit_pad = QPushButton("外扩留边")
        self.btn_fit_pad.clicked.connect(lambda: self._apply_aspect_action("pad"))
        self.btn_pad_margin = QPushButton("+10%画布")
        self.btn_pad_margin.clicked.connect(self._expand_crop_margin)
        self.btn_reset_crop = QPushButton("重置")
        self.btn_reset_crop.clicked.connect(self._reset_crop)
        for b in (self.btn_fit_inside, self.btn_fit_pad, self.btn_pad_margin, self.btn_reset_crop):
            row_crop.addWidget(b)
        c1_lay.addLayout(row_crop)

        row_bg = QHBoxLayout()
        row_bg.addWidget(QLabel("外扩背景:"))
        self.bg_btn_group = QButtonGroup(self)
        self.rb_bg_blur = OptionPillButton("高斯模糊")
        self.rb_bg_solid = OptionPillButton("纯色")
        self.rb_bg_blur.setChecked(True)
        self.bg_btn_group.addButton(self.rb_bg_blur)
        self.bg_btn_group.addButton(self.rb_bg_solid)
        self.rb_bg_blur.toggled.connect(self._on_bg_changed)
        row_bg.addWidget(self.rb_bg_blur)
        row_bg.addWidget(self.rb_bg_solid)

        self.slider_blur = NoWheelSlider(Qt.Orientation.Horizontal)
        self.slider_blur.setRange(8, 80)
        self.slider_blur.setValue(35)
        self.slider_blur.valueChanged.connect(self._on_bg_changed)
        row_bg.addWidget(self.slider_blur, 1)

        self.solid_btns_wrap = QWidget()
        sb_lay = QHBoxLayout(self.solid_btns_wrap)
        sb_lay.setContentsMargins(0, 0, 0, 0)
        sb_lay.setSpacing(3)
        for cname, chex in [("瓷白", "#FEFDF9"), ("樱粉", "#FADCFE"), ("薄荷", "#C7F3F6"), ("纯黑", "#000000")]:
            cb = QPushButton(cname)
            cb.setFixedWidth(40)
            cb.setStyleSheet("padding: 3px 2px; font-size: 11px;")
            cb.clicked.connect(lambda _=False, h=chex: self._set_bg_color(h))
            sb_lay.addWidget(cb)
        self.btn_pick_color = QPushButton("取色")
        self.btn_pick_color.setStyleSheet("padding: 3px 6px; font-size: 11px;")
        self.btn_pick_color.clicked.connect(self._pick_bg_color)
        sb_lay.addWidget(self.btn_pick_color)
        row_bg.addWidget(self.solid_btns_wrap, 1)
        c1_lay.addLayout(row_bg)
        cards_lay.addWidget(c1)

        # Card 02: FPS & RIFE AI Interpolation
        c2, c2_lay = _make_section_card("02", "帧率重排与 AI 光流补帧 (类 DLSS / 小黄鸭)")
        row_fps_modes = QHBoxLayout()
        row_fps_modes.setSpacing(6)
        self.fps_btn_group = QButtonGroup(self)
        self.rb_keep = OptionPillButton("保持原帧率")
        self.rb_retime = OptionPillButton("原始帧重排")
        self.rb_rife = OptionPillButton("AI 光流补帧")
        self.rb_keep.setChecked(True)
        for rb in (self.rb_keep, self.rb_retime, self.rb_rife):
            self.fps_btn_group.addButton(rb)
            rb.toggled.connect(self._on_fps_mode_changed)
            row_fps_modes.addWidget(rb, 1)
        c2_lay.addLayout(row_fps_modes)

        self.rife_panel = QWidget()
        rife_lay = QVBoxLayout(self.rife_panel)
        rife_lay.setContentsMargins(0, 2, 0, 0)
        rife_lay.setSpacing(4)
        rife_row1 = QHBoxLayout()
        rife_row1.addWidget(QLabel("倍率:"))
        self.combo_rife_mult = NoWheelComboBox()
        self.combo_rife_mult.addItem("2x (帧数翻倍)", 2)
        self.combo_rife_mult.addItem("3x", 3)
        self.combo_rife_mult.addItem("4x", 4)
        self.combo_rife_mult.currentIndexChanged.connect(self._update_summary)
        rife_row1.addWidget(self.combo_rife_mult)
        rife_row1.addWidget(QLabel("模型:"))
        self.combo_rife_model = NoWheelComboBox()
        self._populate_rife_models()
        rife_row1.addWidget(self.combo_rife_model, 1)
        self.btn_dl_rife = QPushButton("⬇ 下载 RIFE (~22MB)")
        self.btn_dl_rife.setVisible(find_rife() is None)
        self.btn_dl_rife.clicked.connect(self._on_download_rife)
        rife_row1.addWidget(self.btn_dl_rife)
        rife_lay.addLayout(rife_row1)

        rife_row2 = QHBoxLayout()
        rife_row2.addWidget(QLabel("补帧后播放帧率:"))
        self.combo_rife_policy = NoWheelComboBox()
        self.combo_rife_policy.addItem("保持原时长 (自动倍增帧率)", "keep_duration")
        self.combo_rife_policy.addItem("自定义播放帧率 (重排慢放时长)", "custom_fps")
        self.combo_rife_policy.currentIndexChanged.connect(self._on_fps_mode_changed)
        rife_row2.addWidget(self.combo_rife_policy, 1)
        rife_lay.addLayout(rife_row2)
        c2_lay.addWidget(self.rife_panel)

        self.custom_fps_panel = QWidget()
        cf_lay = QHBoxLayout(self.custom_fps_panel)
        cf_lay.setContentsMargins(0, 2, 0, 0)
        cf_lay.setSpacing(5)
        cf_lay.addWidget(QLabel("目标帧率 (FPS):"))
        self.spin_custom_fps = NoWheelDoubleSpinBox()
        self.spin_custom_fps.setRange(1.0, 240.0)
        self.spin_custom_fps.setDecimals(2)
        self.spin_custom_fps.setValue(30.0)
        self.spin_custom_fps.valueChanged.connect(self._on_custom_fps_changed)
        cf_lay.addWidget(self.spin_custom_fps)
        for preset_fps in FPS_PRESET_VALUES:
            btn = QPushButton(f"{preset_fps}")
            btn.setObjectName("fpsChipBtn")
            btn.setCheckable(True)
            btn.setMinimumWidth(44)
            btn.clicked.connect(lambda _=False, v=preset_fps: self.spin_custom_fps.setValue(float(v)))
            cf_lay.addWidget(btn)
            self._fps_chip_buttons.append((preset_fps, btn))
        c2_lay.addWidget(self.custom_fps_panel)

        sum_row = QHBoxLayout()
        self.lbl_summary = QLabel("请先打开视频")
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setStyleSheet(
            f"background-color: {PALETTE['mint_soft']}; border: 1px solid {PALETTE['sky']}; border-radius: 8px; padding: 6px 9px; color: {PALETTE['ink_primary']};"
        )
        sum_row.addWidget(self.lbl_summary, 1)
        self.btn_fit_3s = QPushButton("⚡ 控时 ≤2.95s")
        self.btn_fit_3s.setObjectName("keyStarBtn")
        self.btn_fit_3s.setVisible(False)
        self.btn_fit_3s.setToolTip("自动调整目标帧率使实况总时长 ≤ 2.95 秒")
        self.btn_fit_3s.clicked.connect(self._fit_duration_under_3s)
        sum_row.addWidget(self.btn_fit_3s)
        c2_lay.addLayout(sum_row)
        cards_lay.addWidget(c2)

        # Card 03: Custom Key Photo
        c3, c3_lay = _make_section_card("03", "实况封面 (主要图片)")
        c3_body = QHBoxLayout()
        c3_body.setSpacing(8)
        key_left = QVBoxLayout()
        key_left.setSpacing(5)
        key_mode_row = QHBoxLayout()
        self.key_btn_group = QButtonGroup(self)
        self.rb_key_video = OptionPillButton("使用视频选定帧")
        self.rb_key_ext = OptionPillButton("使用外部自定义图片")
        self.rb_key_video.setChecked(True)
        self.key_btn_group.addButton(self.rb_key_video)
        self.key_btn_group.addButton(self.rb_key_ext)
        self.rb_key_video.toggled.connect(self._on_key_source_changed)
        key_mode_row.addWidget(self.rb_key_video, 1)
        key_mode_row.addWidget(self.rb_key_ext, 1)
        key_left.addLayout(key_mode_row)

        row_kf = QHBoxLayout()
        row_kf.addWidget(QLabel("封面帧:"))
        self.spin_key_frame = NoWheelSpinBox()
        self.spin_key_frame.valueChanged.connect(self._on_key_frame_changed)
        row_kf.addWidget(self.spin_key_frame)
        self.btn_take_cur_key = QPushButton("取当前帧")
        self.btn_take_cur_key.clicked.connect(self._set_current_as_key)
        row_kf.addWidget(self.btn_take_cur_key)
        self.btn_choose_ext = QPushButton("选图片...")
        self.btn_choose_ext.clicked.connect(self._on_choose_external_key)
        row_kf.addWidget(self.btn_choose_ext)
        self.lbl_ext_name = QLabel("未选")
        self.lbl_ext_name.setStyleSheet(f"color: {PALETTE['ink_muted']};")
        row_kf.addWidget(self.lbl_ext_name, 1)
        key_left.addLayout(row_kf)

        self.lbl_key_thumb = QLabel("封面")
        self.lbl_key_thumb.setFixedSize(64, 68)
        self.lbl_key_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_key_thumb.setStyleSheet(
            f"background-color: {PALETTE['surface_subtle']}; border: 1px solid {PALETTE['sky']}; border-radius: 7px;"
        )
        c3_body.addLayout(key_left, 1)
        c3_body.addWidget(self.lbl_key_thumb)
        c3_lay.addLayout(c3_body)
        cards_lay.addWidget(c3)

        # Card 04: Export & Custom Auto-Increment DCF
        c4, c4_lay = _make_section_card("04", "导出设置与 U 盘直导 iPhone")
        row_fmt = QHBoxLayout()
        row_fmt.addWidget(QLabel("格式:"))
        self.combo_img_fmt = NoWheelComboBox()
        self.combo_img_fmt.addItem("HEIC + MOV (iPhone 原生推荐)", "heic")
        self.combo_img_fmt.addItem("JPG + MOV (兼容旧工具)", "jpg")
        self.combo_img_fmt.currentIndexChanged.connect(self._update_export_preview)
        row_fmt.addWidget(self.combo_img_fmt, 1)
        row_fmt.addWidget(QLabel("视频长边:"))
        self.combo_max_edge = NoWheelComboBox()
        self.combo_max_edge.addItem("不缩放原尺寸 (原画质默认)", 0)
        self.combo_max_edge.addItem("长边 ≤1920px", 1920)
        self.combo_max_edge.addItem("长边 ≤1440px", 1440)
        row_fmt.addWidget(self.combo_max_edge)
        c4_lay.addLayout(row_fmt)

        row_exp_mode = QHBoxLayout()
        self.export_btn_group = QButtonGroup(self)
        self.rb_export_usb = OptionPillButton("U盘相机结构 (DCIM/100APPLE)")
        self.rb_export_folder = OptionPillButton("普通文件夹 (JPG/HEIC+MOV)")
        self.rb_export_usb.setChecked(True)
        self.export_btn_group.addButton(self.rb_export_usb)
        self.export_btn_group.addButton(self.rb_export_folder)
        self.rb_export_usb.toggled.connect(self._on_export_mode_changed)
        row_exp_mode.addWidget(self.rb_export_usb, 1)
        row_exp_mode.addWidget(self.rb_export_folder, 1)
        c4_lay.addLayout(row_exp_mode)

        self.usb_panel = QWidget()
        usb_lay = QVBoxLayout(self.usb_panel)
        usb_lay.setContentsMargins(0, 2, 0, 0)
        usb_lay.setSpacing(4)
        row_drv = QHBoxLayout()
        self.combo_usb = NoWheelComboBox()
        self.combo_usb.currentIndexChanged.connect(self._update_export_preview)
        self.btn_refresh_usb = QPushButton("刷新U盘")
        self.btn_refresh_usb.clicked.connect(self._refresh_usb_drives)
        self.btn_browse_usb = QPushButton("自选目录...")
        self.btn_browse_usb.clicked.connect(self._browse_usb_root)
        row_drv.addWidget(self.combo_usb, 1)
        row_drv.addWidget(self.btn_refresh_usb)
        row_drv.addWidget(self.btn_browse_usb)
        usb_lay.addLayout(row_drv)

        row_dcf = QHBoxLayout()
        row_dcf.addWidget(QLabel("相机编号:"))
        self.edit_dcf_prefix = QLineEdit("IMG_")
        self.edit_dcf_prefix.setFixedWidth(56)
        self.edit_dcf_prefix.setMaxLength(4)
        self.edit_dcf_prefix.setValidator(QRegularExpressionValidator(r"[A-Za-z0-9_]{1,4}"))
        self.edit_dcf_prefix.textChanged.connect(self._on_dcf_changed)
        row_dcf.addWidget(self.edit_dcf_prefix)

        self.spin_dcf = FourDigitSpinBox()
        self.spin_dcf.setRange(1, 9999)
        self.spin_dcf.setValue(7607)
        self.spin_dcf.valueChanged.connect(self._on_dcf_changed)
        row_dcf.addWidget(self.spin_dcf)

        self.btn_dcf_from_file = QPushButton("取文件名号")
        self.btn_dcf_from_file.setStyleSheet("padding: 3px 6px; font-size: 11px;")
        self.btn_dcf_from_file.clicked.connect(self._reset_dcf_from_filename)
        row_dcf.addWidget(self.btn_dcf_from_file)

        self.chk_dcf_auto_inc = StyledCheckBox("导出后自动+1")
        self.chk_dcf_auto_inc.setChecked(True)
        self.chk_dcf_auto_inc.toggled.connect(self._save_dcf_settings)
        row_dcf.addWidget(self.chk_dcf_auto_inc)
        usb_lay.addLayout(row_dcf)

        self.lbl_usb_dest_preview = QLabel("")
        self.lbl_usb_dest_preview.setStyleSheet(f"color: {PALETTE['ink_secondary']}; font-size: 11px;")
        usb_lay.addWidget(self.lbl_usb_dest_preview)
        c4_lay.addWidget(self.usb_panel)

        self.folder_panel = QWidget()
        fol_lay = QHBoxLayout(self.folder_panel)
        fol_lay.setContentsMargins(0, 2, 0, 0)
        self.edit_folder = QLineEdit(str(Path.home() / "Desktop" / "LivePhoto_Output"))
        self.btn_browse_folder = QPushButton("浏览...")
        self.btn_browse_folder.clicked.connect(self._browse_output_folder)
        self.chk_livp = StyledCheckBox(".livp")
        fol_lay.addWidget(self.edit_folder, 1)
        fol_lay.addWidget(self.btn_browse_folder)
        fol_lay.addWidget(self.chk_livp)
        c4_lay.addWidget(self.folder_panel)

        self.chk_hw_encode = StyledCheckBox(
            "NVIDIA NVENC GPU 硬件编码" if self._nvenc_available else "CPU x264 编码 (未检测到 NVENC)"
        )
        self.chk_hw_encode.setChecked(self._nvenc_available)
        self.chk_hw_encode.setEnabled(self._nvenc_available)
        c4_lay.addWidget(self.chk_hw_encode)
        cards_lay.addWidget(c4)
        cards_lay.addStretch(1)

        # Pinned Export Footer
        footer = QFrame()
        footer.setObjectName("exportFooter")
        f_lay = QVBoxLayout(footer)
        f_lay.setContentsMargins(10, 8, 10, 8)
        f_lay.setSpacing(5)

        row_act = QHBoxLayout()
        self.btn_convert = QPushButton("✦ 开始生成实况照片")
        self.btn_convert.setObjectName("primaryBtn")
        self.btn_convert.setEnabled(False)
        self.btn_convert.clicked.connect(self._on_start_convert)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._on_cancel_convert)
        self.btn_open_out = QPushButton("打开目录")
        self.btn_open_out.setEnabled(False)
        self.btn_open_out.clicked.connect(self._open_last_output_dir)
        row_act.addWidget(self.btn_convert, 1)
        row_act.addWidget(self.btn_cancel)
        row_act.addWidget(self.btn_open_out)
        f_lay.addLayout(row_act)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        f_lay.addWidget(self.progress_bar)

        self.lbl_status = QLabel("就绪 · U盘插 iPhone 后打开「照片」App → 右下角「导入」即可合成实况")
        self.lbl_status.setStyleSheet(f"color: {PALETTE['ink_secondary']}; font-size: 11px;")
        f_lay.addWidget(self.lbl_status)

        right_col_lay.addWidget(footer)
        body.addWidget(right_col)

        self._on_bg_changed()
        self._on_fps_mode_changed()
        self._on_key_source_changed()
        self._on_export_mode_changed()
        self._on_custom_fps_changed(self.spin_custom_fps.value())

    def _populate_rife_models(self) -> None:
        self.combo_rife_model.clear()
        models = list_available_rife_models()
        if not models:
            self.combo_rife_model.addItem("FFmpeg 光流回退", "ffmpeg_minterpolate")
        else:
            for m in models:
                tag = " (高画质)" if "4.25" in m else (" (极速)" if "4.6" in m else "")
                self.combo_rife_model.addItem(f"{m}{tag}", m)

    def _load_persisted_dcf_settings(self) -> None:
        self._syncing_ui = True
        try:
            pfx = str(self._settings.value("dcf_prefix", "IMG_"))
            num = int(self._settings.value("dcf_number", 7607))
            auto_inc = str(self._settings.value("dcf_auto_inc", "true")).lower() == "true"
            self.edit_dcf_prefix.setText(pfx)
            self.spin_dcf.setValue(max(1, min(9999, num)))
            self.chk_dcf_auto_inc.setChecked(auto_inc)
        finally:
            self._syncing_ui = False

    def _save_dcf_settings(self) -> None:
        if self._syncing_ui:
            return
        self._settings.setValue("dcf_prefix", normalize_dcf_prefix(self.edit_dcf_prefix.text()))
        self._settings.setValue("dcf_number", self.spin_dcf.value())
        self._settings.setValue("dcf_auto_inc", self.chk_dcf_auto_inc.isChecked())

    def _on_dcf_changed(self) -> None:
        self._save_dcf_settings()
        self._update_export_preview()

    def _reset_dcf_from_filename(self) -> None:
        if self._video_info is None:
            return
        self.spin_dcf.setValue(extract_4digit_seed(self._video_info.path.stem))

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self._toggle_play)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self._seek_frame(self._current_frame - 1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self._seek_frame(self._current_frame + 1))
        QShortcut(QKeySequence("Shift+Left"), self, lambda: self._seek_frame(self._current_frame - 5))
        QShortcut(QKeySequence("Shift+Right"), self, lambda: self._seek_frame(self._current_frame + 5))
        QShortcut(QKeySequence("["), self, self._set_current_as_start)
        QShortcut(QKeySequence("]"), self, self._set_current_as_end)
        QShortcut(QKeySequence("K"), self, self._set_current_as_key)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}:
                self.load_video(p)
                break

    def _on_open_video(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "选择视频文件",
            str(Path.home() / "Desktop"),
            "视频文件 (*.mp4 *.mov *.mkv *.avi *.m4v *.webm);;所有文件 (*.*)",
        )
        if path_str:
            self.load_video(Path(path_str))

    def load_video(self, path: Path) -> None:
        try:
            info = probe_video(path)
        except Exception as exc:
            QMessageBox.critical(self, "无法读取视频", str(exc))
            return

        self._is_playing = False
        self._play_timer.stop()
        self.btn_play.setText("▶ 播放")
        self._video_info = info
        self._frame_provider.load(info)

        self._syncing_ui = True
        try:
            self.lbl_video_info.setText(
                f"{info.path.name} · {info.width}×{info.height} · {info.fps:.2f} fps · {info.nb_frames} 帧 · {info.duration:.2f}s"
            )
            max_idx = max(0, info.nb_frames - 1)
            self.spin_start.setRange(0, max_idx)
            self.spin_end.setRange(0, max_idx)
            self.spin_key_frame.setRange(0, max_idx)
            self.spin_start.setValue(0)
            self.spin_end.setValue(max_idx)
            self.spin_key_frame.setValue(min(max_idx, max_idx // 2))

            self.combo_aspect.setCurrentIndex(0)
            self.btn_crop_toggle.setChecked(False)
            self.preview.set_crop_editing(False)
            self.preview.reset_view()
            self.preview.set_crop(CropRect(0, 0, info.width, info.height))
            self.lbl_crop_res.setText(f"画布: {info.width}×{info.height}")
            self.btn_convert.setEnabled(True)
        finally:
            self._syncing_ui = False

        self.timeline.configure(info.nb_frames, 0, max(0, info.nb_frames - 1), 0, self.spin_key_frame.value())
        self._seek_frame(0)
        self._refresh_key_thumbnail()
        self._update_summary()
        self._update_export_preview()

    def _on_proxy_frame_decoded(self, idx: int) -> None:
        if idx == self._current_frame and self._video_info is not None:
            qimg = self._frame_provider.get_frame(idx)
            self.preview.set_frame(qimg, self._video_info.width, self._video_info.height, idx)

    def _seek_frame(self, frame_idx: int) -> None:
        if self._video_info is None:
            return
        frame_idx = max(0, min(frame_idx, self._video_info.nb_frames - 1))
        self._current_frame = frame_idx
        qimg = self._frame_provider.get_frame(frame_idx)
        self.preview.set_frame(qimg, self._video_info.width, self._video_info.height, frame_idx)
        self.timeline.set_current_frame(frame_idx)
        self._update_playhead_label()

    def _update_playhead_label(self) -> None:
        if self._video_info is None:
            return
        summary = compute_timeline_summary(self._video_info, self._build_job())
        s = self.spin_start.value()
        e = self.spin_end.value()
        span = max(1, e - s)
        rel = max(0.0, min(1.0, (self._current_frame - s) / span))
        out_sec = rel * summary.output_duration
        self.lbl_playhead.setText(
            f"当前: 第 {self._current_frame} 帧  |  当前设定下时刻: {out_sec:.2f}s / {summary.output_duration:.2f}s"
        )

    def _effective_preview_fps(self) -> float:
        if self._video_info is None:
            return 24.0
        job = self._build_job()
        if job.fps_mode == "raw_retime":
            return max(1.0, float(job.custom_fps))
        if job.fps_mode in ("rife_interpolate", "ffmpeg_minterpolate"):
            summary = compute_timeline_summary(self._video_info, job)
            return max(1.0, summary.selected_frames / max(0.05, summary.output_duration))
        return max(1.0, self._video_info.fps)

    def _toggle_play(self) -> None:
        if self._video_info is None:
            return
        if self._is_playing:
            self._is_playing = False
            self._play_timer.stop()
            self.btn_play.setText("▶ 播放")
        else:
            if self._current_frame < self.spin_start.value() or self._current_frame >= self.spin_end.value():
                self._seek_frame(self.spin_start.value())
            self._play_start_frame = self._current_frame
            self._play_clock.restart()
            self._is_playing = True
            self._play_timer.start()
            self.btn_play.setText("⏸ 暂停")

    def _on_play_tick(self) -> None:
        if self._video_info is None:
            return
        s = self.spin_start.value()
        e = max(s, self.spin_end.value())
        span = max(1, e - s + 1)
        elapsed_sec = self._play_clock.elapsed() / 1000.0
        advance = int(elapsed_sec * self._effective_preview_fps())
        nxt = s + ((self._play_start_frame - s + advance) % span)
        if nxt != self._current_frame:
            self._seek_frame(nxt)

    def _on_crop_toggle_changed(self, checked: bool) -> None:
        self.btn_crop_toggle.setText("✓ 完成裁切" if checked else "✂ 裁切画面")
        self.preview.set_crop_editing(checked)

    def _on_zoom_changed(self, zoom: float) -> None:
        pct = int(round(zoom * 100))
        self.btn_zoom_reset.setText(f"{pct}% 重置视图")

    def _on_preview_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        act_crop = menu.addAction("✓ 完成并隐藏裁切框" if self.preview.is_crop_editing() else "✂ 开启裁切框")
        act_crop.triggered.connect(lambda: self.btn_crop_toggle.setChecked(not self.btn_crop_toggle.isChecked()))

        zoom_menu = menu.addMenu("🔍 视图缩放")
        zoom_menu.addAction("适应窗口 (100% 居中重置)").triggered.connect(self.preview.reset_view)
        for z in (0.5, 1.5, 2.0, 4.0):
            zoom_menu.addAction(f"缩放至 {int(z * 100)}%").triggered.connect(
                lambda _=False, zv=z: self.preview.set_zoom(zv)
            )

        backdrop_menu = menu.addMenu("🎨 画布底色")
        backdrop_menu.addAction("柔和浅灰画廊 (#E6ECF8)").triggered.connect(
            lambda: self._set_backdrop(PALETTE["canvas_light"])
        )
        backdrop_menu.addAction("专业深色暗房 (#1E2130)").triggered.connect(
            lambda: self._set_backdrop(PALETTE["canvas_dark"])
        )

        crop_menu = menu.addMenu("📐 快捷裁切与留边")
        for idx, (label, _r) in enumerate(ASPECT_PRESETS):
            crop_menu.addAction(label).triggered.connect(
                lambda _=False, i=idx: self._select_aspect_from_menu(i)
            )
        crop_menu.addSeparator()
        crop_menu.addAction("内裁填满").triggered.connect(lambda: self._apply_aspect_action("inside"))
        crop_menu.addAction("外扩留边").triggered.connect(lambda: self._apply_aspect_action("pad"))
        crop_menu.addAction("外扩 +10% 边距").triggered.connect(self._expand_crop_margin)
        crop_menu.addAction("重置为全画面").triggered.connect(self._reset_crop)

        bg_menu = menu.addMenu("🖼 外扩背景样式")
        bg_menu.addAction("高斯模糊原图").triggered.connect(lambda: self.rb_bg_blur.setChecked(True))
        for cname, chex in [("瓷白 (#FEFDF9)", "#FEFDF9"), ("樱花粉 (#FADCFE)", "#FADCFE"), ("冰薄荷 (#C7F3F6)", "#C7F3F6"), ("纯黑 (#000000)", "#000000"), ("纯白 (#FFFFFF)", "#FFFFFF")]:
            bg_menu.addAction(cname).triggered.connect(lambda _=False, h=chex: self._set_bg_color(h))
        bg_menu.addAction("自定义取色...").triggered.connect(self._pick_bg_color)

        menu.addSeparator()
        menu.addAction("★ 设当前帧为实况封面 (K)").triggered.connect(self._set_current_as_key)
        menu.addAction("[ 设为片段起点").triggered.connect(self._set_current_as_start)
        menu.addAction("] 设为片段终点").triggered.connect(self._set_current_as_end)

        if self._video_info is not None:
            menu.addSeparator()
            menu.addAction("💾 导出当前帧为单张图片...").triggered.connect(self._export_current_frame_image)

        menu.exec(global_pos)

    def _select_aspect_from_menu(self, idx: int) -> None:
        self.combo_aspect.setCurrentIndex(idx)
        self.btn_crop_toggle.setChecked(True)

    def _set_backdrop(self, hex_color: str) -> None:
        self._backdrop_hex = hex_color
        self._settings.setValue("backdrop_hex", hex_color)
        self.preview.set_backdrop_color(hex_color)

    def _export_current_frame_image(self) -> None:
        if self._video_info is None:
            return
        qimg = self._frame_provider.get_frame(self._current_frame)
        if qimg is None:
            return
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出当前预览帧",
            str(Path.home() / "Desktop" / f"{self._video_info.path.stem}_frame{self._current_frame}.png"),
            "PNG 图片 (*.png);;JPEG 图片 (*.jpg)",
        )
        if out_path:
            qimg.save(out_path)
            self.lbl_status.setText(f"已导出单帧: {Path(out_path).name}")

    def _set_current_as_start(self) -> None:
        if self._video_info is None:
            return
        self.spin_start.setValue(min(self._current_frame, self.spin_end.value()))

    def _set_current_as_end(self) -> None:
        if self._video_info is None:
            return
        self.spin_end.setValue(max(self._current_frame, self.spin_start.value()))

    def _set_current_as_key(self) -> None:
        if self._video_info is None:
            return
        self.rb_key_video.setChecked(True)
        clamped = max(self.spin_start.value(), min(self._current_frame, self.spin_end.value()))
        self.spin_key_frame.setValue(clamped)

    def _on_slider_range_changed(self, start: int, end: int) -> None:
        self._syncing_ui = True
        try:
            self.spin_start.setValue(start)
            self.spin_end.setValue(end)
            kf = max(start, min(self.spin_key_frame.value(), end))
            self.spin_key_frame.setValue(kf)
            self.timeline.set_key_frame(kf, self.rb_key_video.isChecked())
        finally:
            self._syncing_ui = False
        self._refresh_key_thumbnail()
        self._update_summary()

    def _on_range_spin_changed(self) -> None:
        if self._syncing_ui or self._video_info is None:
            return
        start = self.spin_start.value()
        end = self.spin_end.value()
        if start > end:
            end = start
            self._syncing_ui = True
            self.spin_end.setValue(end)
            self._syncing_ui = False
        kf = max(start, min(self.spin_key_frame.value(), end))
        if kf != self.spin_key_frame.value():
            self.spin_key_frame.setValue(kf)
        self.timeline.configure(self._video_info.nb_frames, start, end, self._current_frame, kf)
        self._update_summary()

    def _on_crop_changed(self, crop: CropRect) -> None:
        if self._video_info is None:
            return
        oob = " (外扩)" if crop.is_out_of_bounds(self._video_info.width, self._video_info.height) else ""
        self.lbl_crop_res.setText(f"画布: {crop.w}×{crop.h}{oob}")
        self._refresh_key_thumbnail()

    def _resolve_aspect_ratio(self) -> float | None:
        if self._video_info is None:
            return None
        idx = self.combo_aspect.currentIndex()
        _, ratio = ASPECT_PRESETS[idx]
        if idx == 0:
            return self._video_info.width / self._video_info.height
        if ratio is not None and ratio < 0:
            return None
        return ratio

    def _apply_aspect_action(self, fit_mode: str) -> None:
        if self._video_info is None:
            return
        if not self.btn_crop_toggle.isChecked():
            self.btn_crop_toggle.setChecked(True)
        self.preview.apply_aspect_fit(self._resolve_aspect_ratio(), mode=fit_mode)

    def _expand_crop_margin(self) -> None:
        if self._video_info is None:
            return
        if not self.btn_crop_toggle.isChecked():
            self.btn_crop_toggle.setChecked(True)
        c = self.preview.get_crop()
        nw = int(round(c.w * 1.10))
        nh = int(round(c.h * 1.10))
        nx = c.x - (nw - c.w) // 2
        ny = c.y - (nh - c.h) // 2
        self.preview.set_crop(CropRect(nx, ny, nw, nh), emit=True)

    def _reset_crop(self) -> None:
        if self._video_info is None:
            return
        self.combo_aspect.setCurrentIndex(0)
        self.preview.set_crop(CropRect(0, 0, self._video_info.width, self._video_info.height), emit=True)

    def _on_bg_changed(self) -> None:
        is_blur = self.rb_bg_blur.isChecked()
        self.slider_blur.setVisible(is_blur)
        self.solid_btns_wrap.setVisible(not is_blur)
        self.preview.set_background_style(
            "blur" if is_blur else "solid",
            self._bg_color_hex,
            self.slider_blur.value(),
        )

    def _set_bg_color(self, hex_col: str) -> None:
        self._bg_color_hex = hex_col.upper()
        self._settings.setValue("bg_color", self._bg_color_hex)
        self.rb_bg_solid.setChecked(True)
        self._on_bg_changed()

    def _pick_bg_color(self) -> None:
        c = QColorDialog.getColor(QColor(self._bg_color_hex), self, "选择外扩背景纯色")
        if c.isValid():
            self._set_bg_color(c.name().upper())

    def _on_fps_mode_changed(self) -> None:
        is_retime = self.rb_retime.isChecked()
        is_rife = self.rb_rife.isChecked()
        self.rife_panel.setVisible(is_rife)
        show_custom_fps = is_retime or (is_rife and self.combo_rife_policy.currentData() == "custom_fps")
        self.custom_fps_panel.setVisible(show_custom_fps)
        self._update_summary()

    def _on_custom_fps_changed(self, val: float) -> None:
        for preset_val, btn in self._fps_chip_buttons:
            btn.setChecked(abs(val - preset_val) < 0.01)
        self._update_summary()

    def _fit_duration_under_3s(self) -> None:
        if self._video_info is None:
            return
        job = self._build_job()
        s = compute_timeline_summary(self._video_info, job)
        needed_fps = min(240.0, max(1.0, round(s.output_frames / 2.95 + 0.05, 2)))
        if self.rb_keep.isChecked():
            self.rb_retime.setChecked(True)
        elif self.rb_rife.isChecked():
            self.combo_rife_policy.setCurrentIndex(1)
        self.spin_custom_fps.setValue(needed_fps)

    def _on_download_rife(self) -> None:
        self.btn_dl_rife.setEnabled(False)
        self.lbl_status.setText("正在后台下载 RIFE 补帧引擎...")
        self._rife_worker = RifeDownloadWorker()
        self._rife_worker.progress.connect(lambda m: self.lbl_status.setText(m))
        self._rife_worker.succeeded.connect(self._on_rife_downloaded)
        self._rife_worker.failed.connect(self._on_rife_download_failed)
        self._rife_worker.start()

    def _on_rife_downloaded(self) -> None:
        self._populate_rife_models()
        self.btn_dl_rife.setVisible(find_rife() is None)
        self.lbl_status.setText("RIFE 补帧引擎下载完成！")

    def _on_rife_download_failed(self, err: str) -> None:
        self.btn_dl_rife.setEnabled(True)
        self.lbl_status.setText(f"RIFE 下载失败: {err}")
        QMessageBox.warning(self, "下载 RIFE 失败", err)

    def _on_key_source_changed(self) -> None:
        is_video = self.rb_key_video.isChecked()
        self.spin_key_frame.setEnabled(is_video)
        self.btn_take_cur_key.setEnabled(is_video)
        self.btn_choose_ext.setEnabled(not is_video)
        self.timeline.set_key_frame(self.spin_key_frame.value(), is_video)
        self._refresh_key_thumbnail()
        self._update_summary()

    def _on_key_frame_changed(self, val: int) -> None:
        if self._video_info is None:
            return
        clamped = max(self.spin_start.value(), min(val, self.spin_end.value()))
        if clamped != val:
            self.spin_key_frame.setValue(clamped)
            return
        self.timeline.set_key_frame(clamped, self.rb_key_video.isChecked())
        self._refresh_key_thumbnail()
        self._update_summary()

    def _on_choose_external_key(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "选择外部主要图片",
            str(Path.home() / "Desktop"),
            "图片文件 (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff)",
        )
        if path_str:
            self._external_key_path = Path(path_str)
            self.lbl_ext_name.setText(self._external_key_path.name)
            self.rb_key_ext.setChecked(True)
            self._refresh_key_thumbnail()

    def _refresh_key_thumbnail(self) -> None:
        if self.rb_key_ext.isChecked() and self._external_key_path and self._external_key_path.is_file():
            pix = QPixmap(str(self._external_key_path))
            if not pix.isNull():
                self.lbl_key_thumb.setPixmap(
                    pix.scaled(60, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
                return
        if self._video_info is not None:
            qimg = self._frame_provider.get_frame(self.spin_key_frame.value())
            if qimg is not None:
                pix = QPixmap.fromImage(qimg)
                self.lbl_key_thumb.setPixmap(
                    pix.scaled(60, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
                return
        self.lbl_key_thumb.setText("封面")

    def _on_export_mode_changed(self) -> None:
        is_usb = self.rb_export_usb.isChecked()
        self.usb_panel.setVisible(is_usb)
        self.folder_panel.setVisible(not is_usb)
        self._update_export_preview()

    def _refresh_usb_drives(self) -> None:
        self.combo_usb.clear()
        drives = list_usb_drives(include_system_drive=False)
        for d in drives:
            self.combo_usb.addItem(d.display_name, str(d.root))
        if not drives:
            self.combo_usb.addItem("未检测到 U盘 (请插入后点刷新，或自选目录)", "")
        self._update_export_preview()

    def _browse_usb_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择 U 盘根目录 (将在其下创建 DCIM/100APPLE)")
        if folder:
            self.combo_usb.insertItem(0, f"[自选] {folder}", folder)
            self.combo_usb.setCurrentIndex(0)

    def _browse_output_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择导出文件夹")
        if folder:
            self.edit_folder.setText(folder)

    def _update_export_preview(self) -> None:
        if self.rb_export_usb.isChecked():
            root = str(self.combo_usb.currentData() or "")
            pfx = normalize_dcf_prefix(self.edit_dcf_prefix.text())
            num = self.spin_dcf.value()
            ext = "HEIC" if self.combo_img_fmt.currentData() == "heic" else "JPG"
            if not root:
                self.lbl_usb_dest_preview.setText("目标路径: 请先插入 U盘 或点击「自选目录」")
                self.lbl_usb_dest_preview.setToolTip("")
            else:
                full = str(Path(root) / "DCIM" / "100APPLE" / f"{pfx}{num:04d}.{ext}")
                self.lbl_usb_dest_preview.setText(f"目标路径: {full}")
                self.lbl_usb_dest_preview.setToolTip(full)

    def _build_job(self) -> ConversionJob:
        assert self._video_info is not None
        if self.rb_keep.isChecked():
            fps_mode = "keep_original"
        elif self.rb_retime.isChecked():
            fps_mode = "raw_retime"
        else:
            model_val = self.combo_rife_model.currentData()
            fps_mode = (
                "ffmpeg_minterpolate"
                if (model_val == "ffmpeg_minterpolate" or find_rife() is None)
                else "rife_interpolate"
            )

        is_usb = self.rb_export_usb.isChecked()
        usb_data = str(self.combo_usb.currentData() or "")
        out_root = Path(usb_data) if is_usb else Path(self.edit_folder.text().strip())
        max_edge_val = self.combo_max_edge.currentData()
        return ConversionJob(
            video_path=self._video_info.path,
            start_frame=self.spin_start.value(),
            end_frame=self.spin_end.value(),
            crop=self.preview.get_crop(),
            mute=self.chk_mute.isChecked(),
            fps_mode=fps_mode,  # type: ignore[arg-type]
            custom_fps=self.spin_custom_fps.value(),
            rife_multiplier=int(self.combo_rife_mult.currentData() or 2),
            rife_model=str(self.combo_rife_model.currentData() or "rife-v4.25"),
            rife_playback_policy=str(self.combo_rife_policy.currentData() or "keep_duration"),  # type: ignore[arg-type]
            audio_retime_mode=str(self.combo_audio_retime.currentData() or "speed_sync"),  # type: ignore[arg-type]
            key_source="video_frame" if self.rb_key_video.isChecked() else "external_image",
            key_frame_index=self.spin_key_frame.value(),
            external_key_image=self._external_key_path,
            export_mode="usb_dcim" if is_usb else "folder",
            output_root=out_root,
            dcf_prefix=normalize_dcf_prefix(self.edit_dcf_prefix.text()),
            dcf_number=self.spin_dcf.value(),
            also_pack_livp=self.chk_livp.isChecked(),
            image_format=str(self.combo_img_fmt.currentData() or "heic"),  # type: ignore[arg-type]
            video_max_edge=0 if max_edge_val is None else int(max_edge_val),
            use_hw_encode=self.chk_hw_encode.isChecked(),
            bg_mode="blur" if self.rb_bg_blur.isChecked() else "solid",
            bg_blur_sigma=self.slider_blur.value(),
            bg_color=self._bg_color_hex,
        )

    def _update_summary(self) -> None:
        if self._video_info is None:
            return
        job = self._build_job()
        s = compute_timeline_summary(self._video_info, job)
        self.timeline.set_timing_context(s.output_duration, s.cover_still_time)
        self._update_playhead_label()

        dur = s.output_duration
        self.btn_fit_3s.setVisible(dur > 3.0)
        if dur > 3.0:
            bg = PALETTE["sakura"]
            bd = PALETTE["sakura_border"]
            fg = PALETTE["sakura_ink"]
            note = "\n⚠ 时长超过 3.0s，建议点击右侧「⚡ 控时 ≤2.95s」"
        elif dur < 1.0:
            bg = PALETTE["sakura"]
            bd = PALETTE["sakura_border"]
            fg = PALETTE["sakura_ink"]
            note = "\n⚠ 时长短于 1.0s，建议降低帧率或开启 AI 补帧"
        else:
            bg = PALETTE["mint_soft"]
            bd = PALETTE["sky"]
            fg = PALETTE["ink_primary"]
            note = ""

        self.lbl_summary.setStyleSheet(
            f"background-color: {bg}; border: 1px solid {bd}; border-radius: 8px; padding: 6px 9px; color: {fg};"
        )
        self.lbl_summary.setText(
            f"截取原始帧: {s.selected_frames} 帧  ➔  输出: {s.output_frames} 帧 @ {s.output_fps:.2f} fps\n"
            f"最终实况时长: {dur:.2f} 秒  |  封面锚点: {s.cover_still_time:.2f} 秒{note}"
        )

    def _on_start_convert(self) -> None:
        if self._video_info is None:
            return
        if self.rb_export_usb.isChecked() and not str(self.combo_usb.currentData() or "").strip():
            QMessageBox.warning(self, "未选择 U 盘", "请插入 U 盘后点击「刷新U盘」，或点击「自选目录...」指定目标目录。")
            return
        if self.rb_key_ext.isChecked() and (self._external_key_path is None or not self._external_key_path.is_file()):
            QMessageBox.warning(self, "缺少主要图片", "您选择了外部自定义图片，请先选择一张图片文件。")
            return

        job = self._build_job()
        self.btn_convert.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_open_out.setEnabled(False)
        self.progress_bar.setValue(0)
        self.lbl_status.setText("正在启动转换任务...")

        self._worker = ConversionWorker(job)
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.succeeded.connect(self._on_worker_succeeded)
        self._worker.cancelled.connect(self._on_worker_cancelled)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    def _on_cancel_convert(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            self.btn_cancel.setEnabled(False)
            self.lbl_status.setText("正在取消...")

    def _on_worker_progress(self, pct: int, msg: str) -> None:
        self.progress_bar.setValue(pct)
        self.lbl_status.setText(msg)

    def _on_worker_succeeded(self, result: ConversionResult) -> None:
        self._last_result = result
        self.btn_convert.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_open_out.setEnabled(True)
        self.progress_bar.setValue(100)

        if result.used_dcf_number is not None and self.chk_dcf_auto_inc.isChecked():
            next_num = (result.used_dcf_number % 9999) + 1
            self.spin_dcf.setValue(next_num)

        self.lbl_status.setText(
            f"生成成功！{result.jpg_path.name} + {result.mov_path.name} (UUID: {result.asset_id[:8]}...)"
        )
        QMessageBox.information(
            self,
            "实况照片生成成功",
            f"已导出配对实况照片:\n{result.jpg_path}\n{result.mov_path}\n\n"
            f"输出规格: {result.summary.output_frames} 帧 @ {result.summary.output_fps:.2f} fps ({result.summary.output_duration:.2f}s)",
        )

    def _on_worker_cancelled(self) -> None:
        self.btn_convert.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress_bar.setValue(0)
        self.lbl_status.setText("已取消转换")

    def _on_worker_failed(self, err: str) -> None:
        self.btn_convert.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.lbl_status.setText(f"生成失败: {err}")
        QMessageBox.critical(self, "转换失败", err)

    def _open_last_output_dir(self) -> None:
        if self._last_result is None:
            return
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(self._last_result.jpg_path)])
        else:
            os.startfile(str(self._last_result.jpg_path.parent))  # type: ignore[attr-defined]

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._play_timer.stop()
        self._frame_provider.stop()
        super().closeEvent(event)
