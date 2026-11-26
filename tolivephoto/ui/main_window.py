"""Main PySide6 desktop studio window for ToLivePhoto v2.0."""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
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
    QApplication,
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

from ..core.exif_io import (
    SIDECAR_EXIF_EXTENSIONS,
    ExifMetadata,
    find_matching_sidecar_exif_file,
    read_exif_from_file,
)
from ..core.models import (
    POLAROID_PRESETS,
    ConversionJob,
    CropRect,
    FramePadding,
    VideoInfo,
    compute_3s_trim_around_key,
    compute_polaroid_layout,
    compute_timeline_summary,
    estimate_output_size_mb,
    format_frames_as_sec_frames,
    format_sec_frames,
)
from ..core.pipeline import ConversionResult
from ..core.probe import probe_video
from ..core.tools import find_rife, has_nvenc, list_available_rife_models, list_vulkan_gpus
from ..core.usb_export import extract_4digit_seed, list_usb_drives, normalize_dcf_prefix
from .crop_preview_widget import CropPreviewWidget
from .exif_dialog import ExifEditorDialog
from .frame_provider import PreviewFrameProvider
from .icon import create_app_icon
from .range_slider import RangeTimelineSlider
from .theme import PALETTE, build_pastel_stylesheet, compute_dpi_font_scale
from .widgets import (
    ChipButton,
    FourDigitSpinBox,
    NoWheelComboBox,
    NoWheelDoubleSpinBox,
    NoWheelSlider,
    NoWheelSpinBox,
    OptionPillButton,
    StyledCheckBox,
)
from .worker import BatchConversionWorker, ConversionWorker, RifeDownloadWorker

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}

ASPECT_PRESETS: list[tuple[str, str, float | None]] = [
    ("原比例", "orig", None),
    ("自由", "free", -1.0),
    ("2:3", "2:3", 2.0 / 3.0),
    ("3:4", "3:4", 3.0 / 4.0),
    ("9:16", "9:16", 9.0 / 16.0),
    ("1:1", "1:1", 1.0),
    ("4:3", "4:3", 4.0 / 3.0),
    ("16:9", "16:9", 16.0 / 9.0),
]

FPS_PRESET_VALUES = (24, 25, 30, 60)
LONG_EDGE_PRESETS = (0, 2160, 1440, 1080, 720)


@dataclass(slots=True)
class QueueItemState:
    info: VideoInfo
    start_frame: int
    end_frame: int
    crop: CropRect
    padding: FramePadding
    polaroid_preset: str | None
    aspect_key: str
    audio_mode: str  # "speed_sync" | "trim_fade" | "mute"
    bg_mode: str     # "blur" | "solid"
    bg_color: str
    bg_blur_sigma: int
    fps_mode: str    # "keep_original" | "raw_retime" | "rife_interpolate"
    rife_mode: str   # "target_fps" | "multiplier"
    rife_multiplier: int
    rife_model: str
    rife_gpu_id: int
    rife_playback_policy: str
    custom_fps: float
    key_source: str  # "video_frame" | "external_image"
    key_frame_index: int
    external_key_path: Path | None
    external_key_aspect_mode: str
    video_max_edge: int
    video_bitrate_mbps: float
    image_format: str
    exif_metadata: ExifMetadata | None


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
        app = QApplication.instance()
        if app is not None:
            app.setStyle("Fusion")
        scale = compute_dpi_font_scale(app)

        self.setWindowTitle("ToLivePhoto — 视频转 iPhone 实况照片工作台")
        self.setWindowIcon(create_app_icon())
        self.resize(1520, 920)
        self.setAcceptDrops(True)
        self.setStyleSheet(build_pastel_stylesheet(scale))

        self._settings = QSettings("Arrow36", "ToLivePhoto")
        self._queue: list[QueueItemState] = []
        self._active_idx: int = -1
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
        self._exif_metadata: ExifMetadata | None = None
        self._last_result: ConversionResult | None = None
        self._worker: ConversionWorker | BatchConversionWorker | None = None
        self._rife_worker: RifeDownloadWorker | None = None
        self._syncing_ui = False

        self._bg_color_hex = str(self._settings.value("bg_color", "#FFFFFF"))
        self._backdrop_hex = str(self._settings.value("backdrop_hex", PALETTE["canvas_porcelain"]))
        self._nvenc_available = has_nvenc()
        self._vulkan_gpus = list_vulkan_gpus()

        self._active_aspect_key = "orig"
        self._active_polaroid: str | None = None
        self._fps_chip_buttons: list[tuple[int, QPushButton]] = []
        self._aspect_buttons: dict[str, ChipButton] = {}
        self._polaroid_buttons: dict[str, ChipButton] = {}
        self._long_edge_buttons: dict[int, ChipButton] = {}

        self._build_ui()
        self._load_persisted_dcf_settings()
        self._bind_shortcuts()
        self._refresh_usb_drives()

        if initial_video is not None and initial_video.is_file():
            self.load_videos([initial_video])
        else:
            default_sample = Path.home() / "Desktop" / "CSI_7607.mp4"
            if default_sample.is_file():
                self.load_videos([default_sample])

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
        lbl_brand.setStyleSheet("font-size: 18px; font-weight: bold; color: #25293A;")
        lbl_pill = QLabel("LIVE STUDIO")
        lbl_pill.setObjectName("livePill")

        self.lbl_video_info = QLabel("未加载视频 (支持拖入多个 MP4 / MOV)")
        self.lbl_video_info.setObjectName("metaCapsule")
        self.btn_open = QPushButton("📂 导入视频 (支持多选)...")
        self.btn_open.clicked.connect(self._on_open_videos)

        h_lay.addWidget(lbl_icon)
        h_lay.addWidget(lbl_brand)
        h_lay.addWidget(lbl_pill)
        h_lay.addSpacing(6)
        h_lay.addWidget(self.lbl_video_info, 1)
        h_lay.addWidget(self.btn_open)
        outer.addWidget(header)

        # ---- MULTI-VIDEO QUEUE STRIP ----
        self.queue_bar = QFrame()
        self.queue_bar.setObjectName("queueBar")
        self.queue_bar.setVisible(False)
        qb_lay = QHBoxLayout(self.queue_bar)
        qb_lay.setContentsMargins(10, 5, 10, 5)
        qb_lay.setSpacing(6)

        self.lbl_queue_title = QLabel("队列 (0):")
        self.lbl_queue_title.setStyleSheet(f"font-weight: bold; color: {PALETTE['ink_secondary']};")
        qb_lay.addWidget(self.lbl_queue_title)

        self.queue_scroll = QScrollArea()
        self.queue_scroll.setWidgetResizable(True)
        self.queue_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.queue_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.queue_scroll.setFixedHeight(34)
        self.queue_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.queue_chips_host = QWidget()
        self.queue_chips_host.setStyleSheet("background: transparent;")
        self.queue_chips_lay = QHBoxLayout(self.queue_chips_host)
        self.queue_chips_lay.setContentsMargins(0, 0, 0, 0)
        self.queue_chips_lay.setSpacing(5)
        self.queue_chips_lay.addStretch(1)
        self.queue_scroll.setWidget(self.queue_chips_host)
        qb_lay.addWidget(self.queue_scroll, 1)

        self.btn_queue_sync = QPushButton("⇄ 同步当前设置到全部")
        self.btn_queue_sync.clicked.connect(self._sync_current_settings_to_all)
        self.btn_queue_clear = QPushButton("清空")
        self.btn_queue_clear.clicked.connect(self._clear_queue)
        qb_lay.addWidget(self.btn_queue_sync)
        qb_lay.addWidget(self.btn_queue_clear)
        outer.addWidget(self.queue_bar)

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
        self.lbl_canvas_hint = QLabel("全分辨率预览 · 滚轮缩放 · 左键拖拽 · 右键菜单")
        self.lbl_canvas_hint.setStyleSheet(f"color: {PALETTE['ink_muted']};")
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

        self.lbl_playhead = QLabel("当前: 第 0 帧  |  当前设定下时刻: 0秒0帧 / 0秒0帧")
        self.lbl_playhead.setStyleSheet(f"color: {PALETTE['ink_secondary']};")
        left_layout.addWidget(self.lbl_playhead)

        body.addWidget(stage_card, 1)

        # ---- RIGHT INSPECTOR + PINNED FOOTER ----
        right_col = QWidget()
        right_col.setFixedWidth(590)
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
        cards_lay.setSpacing(8)
        scroll.setWidget(cards_wrap)
        right_col_lay.addWidget(scroll, 1)

        # Keep hidden spinboxes synced with timeline for clean range control
        self.spin_start = NoWheelSpinBox()
        self.spin_start.valueChanged.connect(self._on_range_spin_changed)
        self.spin_end = NoWheelSpinBox()
        self.spin_end.valueChanged.connect(self._on_range_spin_changed)

        # ---- CARD 01: Audio Buttons, Aspect Buttons + Manual Ratio, Polaroid Presets, Flat Background ----
        c1, c1_lay = _make_section_card("01", "声音、比例裁切与拍立得外扩")

        row_audio = QHBoxLayout()
        row_audio.setSpacing(5)
        row_audio.addWidget(QLabel("声音:"))
        self.audio_btn_group = QButtonGroup(self)
        self.btn_audio_sync = ChipButton("🔊 原声同步变速")
        self.btn_audio_fade = ChipButton("🔉 原声截断淡出")
        self.btn_audio_mute = ChipButton("🔇 静音")
        self.btn_audio_sync.setChecked(True)
        for btn in (self.btn_audio_sync, self.btn_audio_fade, self.btn_audio_mute):
            self.audio_btn_group.addButton(btn)
            btn.toggled.connect(self._update_summary)
            row_audio.addWidget(btn, 1)
        c1_lay.addLayout(row_audio)

        row_ratio = QHBoxLayout()
        row_ratio.setSpacing(4)
        row_ratio.addWidget(QLabel("比例:"))
        self.aspect_btn_group = QButtonGroup(self)
        for label, key, _r in ASPECT_PRESETS:
            ab = ChipButton(label)
            self.aspect_btn_group.addButton(ab)
            ab.clicked.connect(lambda _=False, k=key: self._on_aspect_button_clicked(k))
            row_ratio.addWidget(ab)
            self._aspect_buttons[key] = ab
        self._aspect_buttons["orig"].setChecked(True)
        c1_lay.addLayout(row_ratio)

        row_ratio2 = QHBoxLayout()
        row_ratio2.setSpacing(5)
        row_ratio2.addWidget(QLabel("手动比例:"))
        self.edit_custom_ratio = QLineEdit()
        self.edit_custom_ratio.setPlaceholderText("如 5:4 或 16:10")
        self.edit_custom_ratio.setMaximumWidth(120)
        self.edit_custom_ratio.returnPressed.connect(self._apply_manual_ratio)
        row_ratio2.addWidget(self.edit_custom_ratio)
        self.btn_apply_ratio = QPushButton("应用")
        self.btn_apply_ratio.clicked.connect(self._apply_manual_ratio)
        row_ratio2.addWidget(self.btn_apply_ratio)

        self.btn_fit_inside = ChipButton("内裁填满")
        self.btn_fit_pad = ChipButton("外扩留边")
        self.fit_mode_group = QButtonGroup(self)
        self.fit_mode_group.addButton(self.btn_fit_inside)
        self.fit_mode_group.addButton(self.btn_fit_pad)
        self.btn_fit_inside.setChecked(True)
        self.btn_fit_inside.clicked.connect(lambda: self._reapply_current_aspect("inside"))
        self.btn_fit_pad.clicked.connect(lambda: self._reapply_current_aspect("pad"))
        row_ratio2.addWidget(self.btn_fit_inside)
        row_ratio2.addWidget(self.btn_fit_pad)

        self.btn_pad_margin = QPushButton("+10%画布")
        self.btn_pad_margin.clicked.connect(self._expand_crop_margin)
        self.btn_reset_crop = QPushButton("重置")
        self.btn_reset_crop.clicked.connect(self._reset_crop)
        row_ratio2.addWidget(self.btn_pad_margin)
        row_ratio2.addWidget(self.btn_reset_crop)
        c1_lay.addLayout(row_ratio2)

        row_polaroid = QHBoxLayout()
        row_polaroid.setSpacing(5)
        row_polaroid.addWidget(QLabel("拍立得:"))
        self.polaroid_btn_group = QButtonGroup(self)
        self.polaroid_btn_group.setExclusive(False)
        for pkey, spec in POLAROID_PRESETS.items():
            pb = ChipButton(spec.label)
            pb.clicked.connect(lambda _=False, k=pkey: self._apply_polaroid_preset(k))
            row_polaroid.addWidget(pb, 1)
            self._polaroid_buttons[pkey] = pb
        c1_lay.addLayout(row_polaroid)

        row_bg = QHBoxLayout()
        row_bg.setSpacing(5)
        row_bg.addWidget(QLabel("外扩背景:"))
        self.bg_btn_group = QButtonGroup(self)
        self.btn_bg_blur = ChipButton("高斯模糊")
        self.btn_bg_white = ChipButton("纯白")
        self.btn_bg_black = ChipButton("纯黑")
        self.btn_bg_pick = ChipButton("🎨 取色")
        self.btn_bg_blur.setChecked(True)
        for b in (self.btn_bg_blur, self.btn_bg_white, self.btn_bg_black, self.btn_bg_pick):
            self.bg_btn_group.addButton(b)
            row_bg.addWidget(b)
        self.btn_bg_blur.clicked.connect(self._on_bg_blur_selected)
        self.btn_bg_white.clicked.connect(lambda: self._set_bg_color("#FFFFFF", self.btn_bg_white))
        self.btn_bg_black.clicked.connect(lambda: self._set_bg_color("#000000", self.btn_bg_black))
        self.btn_bg_pick.clicked.connect(self._pick_bg_color)

        self.slider_blur = NoWheelSlider(Qt.Orientation.Horizontal)
        self.slider_blur.setRange(8, 80)
        self.slider_blur.setValue(35)
        self.slider_blur.valueChanged.connect(self._on_bg_changed)
        row_bg.addWidget(self.slider_blur, 1)
        c1_lay.addLayout(row_bg)
        cards_lay.addWidget(c1)

        # ---- CARD 02: FPS Control, RIFE 30fps default, GPU Acceleration, X秒Y帧 & 3s Key-Trim ----
        c2, c2_lay = _make_section_card("02", "帧率控制与 AI 光流补帧 (类 DLSS / 小黄鸭)")
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
        rife_lay.setSpacing(5)

        rife_row1 = QHBoxLayout()
        rife_row1.setSpacing(5)
        rife_row1.addWidget(QLabel("补帧模式:"))
        self.combo_rife_mode = NoWheelComboBox()
        self.combo_rife_mode.addItem("补帧至目标帧率 (保持原时长，默认30帧)", ("target_fps", 1))
        self.combo_rife_mode.addItem("2x 倍率补帧", ("multiplier", 2))
        self.combo_rife_mode.addItem("3x 倍率补帧", ("multiplier", 3))
        self.combo_rife_mode.addItem("4x 倍率补帧", ("multiplier", 4))
        self.combo_rife_mode.currentIndexChanged.connect(self._on_fps_mode_changed)
        rife_row1.addWidget(self.combo_rife_mode, 1)
        self.btn_dl_rife = QPushButton("⬇ 下载 RIFE")
        self.btn_dl_rife.setVisible(find_rife() is None)
        self.btn_dl_rife.clicked.connect(self._on_download_rife)
        rife_row1.addWidget(self.btn_dl_rife)
        rife_lay.addLayout(rife_row1)

        rife_row2 = QHBoxLayout()
        rife_row2.setSpacing(5)
        rife_row2.addWidget(QLabel("模型:"))
        self.combo_rife_model = NoWheelComboBox()
        self._populate_rife_models()
        rife_row2.addWidget(self.combo_rife_model, 1)
        rife_row2.addWidget(QLabel("加速:"))
        self.combo_rife_gpu = NoWheelComboBox()
        self._populate_rife_gpus()
        rife_row2.addWidget(self.combo_rife_gpu, 1)
        rife_lay.addLayout(rife_row2)

        self.rife_policy_row = QWidget()
        rp_lay = QHBoxLayout(self.rife_policy_row)
        rp_lay.setContentsMargins(0, 0, 0, 0)
        rp_lay.addWidget(QLabel("倍率后播放:"))
        self.combo_rife_policy = NoWheelComboBox()
        self.combo_rife_policy.addItem("保持原时长 (自动倍增帧率)", "keep_duration")
        self.combo_rife_policy.addItem("自定义播放帧率 (慢放)", "custom_fps")
        self.combo_rife_policy.currentIndexChanged.connect(self._on_fps_mode_changed)
        rp_lay.addWidget(self.combo_rife_policy, 1)
        rife_lay.addWidget(self.rife_policy_row)
        c2_lay.addWidget(self.rife_panel)

        self.custom_fps_panel = QWidget()
        cf_lay = QHBoxLayout(self.custom_fps_panel)
        cf_lay.setContentsMargins(0, 2, 0, 0)
        cf_lay.setSpacing(5)
        cf_lay.addWidget(QLabel("目标帧率:"))
        self.spin_custom_fps = NoWheelDoubleSpinBox()
        self.spin_custom_fps.setRange(1.0, 240.0)
        self.spin_custom_fps.setDecimals(0)
        self.spin_custom_fps.setValue(30.0)
        self.spin_custom_fps.setSuffix(" 帧/秒")
        self.spin_custom_fps.valueChanged.connect(self._on_custom_fps_changed)
        cf_lay.addWidget(self.spin_custom_fps)
        for preset_fps in FPS_PRESET_VALUES:
            btn = QPushButton(f"{preset_fps}")
            btn.setObjectName("fpsChipBtn")
            btn.setCheckable(True)
            btn.setMinimumWidth(46)
            btn.clicked.connect(lambda _=False, v=preset_fps: self.spin_custom_fps.setValue(float(v)))
            cf_lay.addWidget(btn)
            self._fps_chip_buttons.append((preset_fps, btn))
        c2_lay.addWidget(self.custom_fps_panel)

        sum_row = QHBoxLayout()
        self.lbl_summary = QLabel("请先导入视频")
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setStyleSheet(
            f"background-color: {PALETTE['mint_soft']}; border: 1px solid {PALETTE['sky']}; border-radius: 8px; padding: 6px 10px; color: {PALETTE['ink_primary']};"
        )
        sum_row.addWidget(self.lbl_summary, 1)
        self.btn_fit_3s = QPushButton("✂ 控时3秒")
        self.btn_fit_3s.setObjectName("keyStarBtn")
        self.btn_fit_3s.setToolTip("不改变帧率，自动在主要图片附近裁切 3 秒片段")
        self.btn_fit_3s.clicked.connect(self._fit_duration_under_3s)
        sum_row.addWidget(self.btn_fit_3s)
        c2_lay.addLayout(sum_row)
        cards_lay.addWidget(c2)

        # ---- CARD 03: Key Photo & External Cover Aspect Mismatch Handling ----
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

        self.ext_aspect_panel = QWidget()
        ea_lay = QHBoxLayout(self.ext_aspect_panel)
        ea_lay.setContentsMargins(0, 2, 0, 0)
        ea_lay.setSpacing(4)
        ea_lay.addWidget(QLabel("比例不同:"))
        self.ext_aspect_group = QButtonGroup(self)
        self.btn_ext_crop = ChipButton("居中裁切封面")
        self.btn_ext_pad = ChipButton("留边保留全图")
        self.btn_ext_match = ChipButton("视频随封面比例")
        self.btn_ext_crop.setChecked(True)
        for b, mode_val in (
            (self.btn_ext_crop, "crop_fill"),
            (self.btn_ext_pad, "pad_fit"),
            (self.btn_ext_match, "match_cover_aspect"),
        ):
            self.ext_aspect_group.addButton(b)
            b.clicked.connect(lambda _=False, m=mode_val: self._on_ext_aspect_mode_clicked(m))
            ea_lay.addWidget(b, 1)
        key_left.addWidget(self.ext_aspect_panel)

        self.lbl_key_thumb = QLabel("封面")
        self.lbl_key_thumb.setFixedSize(68, 74)
        self.lbl_key_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_key_thumb.setStyleSheet(
            f"background-color: {PALETTE['surface_subtle']}; border: 1px solid {PALETTE['sky']}; border-radius: 7px;"
        )
        c3_body.addLayout(key_left, 1)
        c3_body.addWidget(self.lbl_key_thumb)
        c3_lay.addLayout(c3_body)
        cards_lay.addWidget(c3)

        # ---- CARD 04: Long-Edge Buttons, Bitrate + Size Estimate, EXIF Import/Edit, USB DCIM ----
        c4, c4_lay = _make_section_card("04", "导出设置、码率预估、EXIF 与 U 盘直导")
        row_fmt = QHBoxLayout()
        row_fmt.addWidget(QLabel("格式:"))
        self.combo_img_fmt = NoWheelComboBox()
        self.combo_img_fmt.addItem("HEIC + MOV (iPhone 原生推荐)", "heic")
        self.combo_img_fmt.addItem("JPG + MOV (兼容旧工具)", "jpg")
        self.combo_img_fmt.currentIndexChanged.connect(self._on_export_param_changed)
        row_fmt.addWidget(self.combo_img_fmt, 1)
        c4_lay.addLayout(row_fmt)

        row_edge = QHBoxLayout()
        row_edge.setSpacing(4)
        row_edge.addWidget(QLabel("视频长边:"))
        self.long_edge_group = QButtonGroup(self)
        for val in LONG_EDGE_PRESETS:
            txt = "原尺寸" if val == 0 else str(val)
            eb = ChipButton(txt)
            self.long_edge_group.addButton(eb)
            eb.clicked.connect(lambda _=False, v=val: self._on_long_edge_preset_clicked(v))
            row_edge.addWidget(eb)
            self._long_edge_buttons[val] = eb
        self._long_edge_buttons[0].setChecked(True)
        self.spin_custom_edge = NoWheelSpinBox()
        self.spin_custom_edge.setRange(0, 8192)
        self.spin_custom_edge.setSingleStep(2)
        self.spin_custom_edge.setSpecialValueText("原尺寸")
        self.spin_custom_edge.setSuffix(" px")
        self.spin_custom_edge.setFixedWidth(96)
        self.spin_custom_edge.valueChanged.connect(self._on_custom_edge_changed)
        row_edge.addWidget(self.spin_custom_edge)
        c4_lay.addLayout(row_edge)

        row_br = QHBoxLayout()
        row_br.setSpacing(6)
        row_br.addWidget(QLabel("输出码率:"))
        self.slider_bitrate = NoWheelSlider(Qt.Orientation.Horizontal)
        self.slider_bitrate.setRange(4, 80)
        self.slider_bitrate.setValue(24)
        self.slider_bitrate.valueChanged.connect(self._on_bitrate_changed)
        row_br.addWidget(self.slider_bitrate, 1)
        self.lbl_bitrate_val = QLabel("24 Mbps")
        self.lbl_bitrate_val.setStyleSheet("font-weight: bold;")
        row_br.addWidget(self.lbl_bitrate_val)
        self.lbl_est_size = QLabel("预计: -")
        self.lbl_est_size.setObjectName("metaCapsule")
        row_br.addWidget(self.lbl_est_size)
        c4_lay.addLayout(row_br)

        row_exif = QHBoxLayout()
        row_exif.setSpacing(5)
        row_exif.addWidget(QLabel("EXIF:"))
        self.lbl_exif_summary = QLabel("默认 Apple 实况信息")
        self.lbl_exif_summary.setObjectName("metaCapsule")
        row_exif.addWidget(self.lbl_exif_summary, 1)
        self.btn_edit_exif = QPushButton("📷 修改 / 导入 EXIF...")
        self.btn_edit_exif.clicked.connect(self._on_open_exif_dialog)
        self.btn_auto_exif = QPushButton("🪄 同名RAW")
        self.btn_auto_exif.clicked.connect(self._on_quick_auto_exif)
        row_exif.addWidget(self.btn_edit_exif)
        row_exif.addWidget(self.btn_auto_exif)
        c4_lay.addLayout(row_exif)

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
        self.edit_dcf_prefix.setFixedWidth(58)
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
        self.btn_dcf_from_file.clicked.connect(self._reset_dcf_from_filename)
        row_dcf.addWidget(self.btn_dcf_from_file)

        self.chk_dcf_auto_inc = StyledCheckBox("导出后自动+1")
        self.chk_dcf_auto_inc.setChecked(True)
        self.chk_dcf_auto_inc.toggled.connect(self._save_dcf_settings)
        row_dcf.addWidget(self.chk_dcf_auto_inc)
        usb_lay.addLayout(row_dcf)

        self.lbl_usb_dest_preview = QLabel("")
        self.lbl_usb_dest_preview.setStyleSheet(f"color: {PALETTE['ink_secondary']};")
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
        f_lay.setSpacing(6)

        row_act = QHBoxLayout()
        self.btn_convert = QPushButton("✦ 开始生成实况照片")
        self.btn_convert.setObjectName("primaryBtn")
        self.btn_convert.setEnabled(False)
        self.btn_convert.clicked.connect(self._on_start_convert)
        self.btn_convert_all = QPushButton("✦ 批量生成全部")
        self.btn_convert_all.setObjectName("keyStarBtn")
        self.btn_convert_all.setVisible(False)
        self.btn_convert_all.clicked.connect(self._on_start_batch_convert)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._on_cancel_convert)
        self.btn_open_out = QPushButton("打开目录")
        self.btn_open_out.setEnabled(False)
        self.btn_open_out.clicked.connect(self._open_last_output_dir)
        row_act.addWidget(self.btn_convert, 1)
        row_act.addWidget(self.btn_convert_all)
        row_act.addWidget(self.btn_cancel)
        row_act.addWidget(self.btn_open_out)
        f_lay.addLayout(row_act)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        f_lay.addWidget(self.progress_bar)

        self.lbl_status = QLabel("就绪 · U盘插 iPhone 后打开「照片」App → 右下角「导入」即可合成实况")
        self.lbl_status.setStyleSheet(f"color: {PALETTE['ink_secondary']};")
        f_lay.addWidget(self.lbl_status)

        right_col_lay.addWidget(footer)
        body.addWidget(right_col)

        self._on_bg_changed()
        self._on_fps_mode_changed()
        self._on_key_source_changed()
        self._on_export_mode_changed()
        self._on_custom_fps_changed(self.spin_custom_fps.value())

    # ---------- Queue & Video Loading ----------

    def _default_item_state(self, info: VideoInfo) -> QueueItemState:
        return QueueItemState(
            info=info,
            start_frame=0,
            end_frame=max(0, info.nb_frames - 1),
            crop=CropRect(0, 0, info.width, info.height),
            padding=FramePadding(),
            polaroid_preset=None,
            aspect_key="orig",
            audio_mode="speed_sync",
            bg_mode="blur" if self.btn_bg_blur.isChecked() else "solid",
            bg_color=self._bg_color_hex,
            bg_blur_sigma=self.slider_blur.value(),
            fps_mode="keep_original",
            rife_mode="target_fps",
            rife_multiplier=2,
            rife_model=str(self.combo_rife_model.currentData() or "rife-v4.25"),
            rife_gpu_id=int(self.combo_rife_gpu.currentData() if self.combo_rife_gpu.currentData() is not None else 0),
            rife_playback_policy="keep_duration",
            custom_fps=30.0,
            key_source="video_frame",
            key_frame_index=min(max(0, info.nb_frames - 1), max(0, info.nb_frames - 1) // 2),
            external_key_path=None,
            external_key_aspect_mode="crop_fill",
            video_max_edge=self.spin_custom_edge.value(),
            video_bitrate_mbps=float(self.slider_bitrate.value()),
            image_format=str(self.combo_img_fmt.currentData() or "heic"),
            exif_metadata=None,
        )

    def _capture_active_state(self) -> None:
        if self._active_idx < 0 or self._active_idx >= len(self._queue) or self._video_info is None:
            return
        st = self._queue[self._active_idx]
        st.start_frame = self.spin_start.value()
        st.end_frame = self.spin_end.value()
        st.crop = self.preview.get_crop()
        st.padding = self.preview.get_padding()
        st.polaroid_preset = self._active_polaroid
        st.aspect_key = self._active_aspect_key
        if self.btn_audio_mute.isChecked():
            st.audio_mode = "mute"
        elif self.btn_audio_fade.isChecked():
            st.audio_mode = "trim_fade"
        else:
            st.audio_mode = "speed_sync"
        st.bg_mode = "blur" if self.btn_bg_blur.isChecked() else "solid"
        st.bg_color = self._bg_color_hex
        st.bg_blur_sigma = self.slider_blur.value()
        if self.rb_keep.isChecked():
            st.fps_mode = "keep_original"
        elif self.rb_retime.isChecked():
            st.fps_mode = "raw_retime"
        else:
            st.fps_mode = "rife_interpolate"
        rm_data = self.combo_rife_mode.currentData() or ("target_fps", 1)
        st.rife_mode = rm_data[0]
        st.rife_multiplier = int(rm_data[1])
        st.rife_model = str(self.combo_rife_model.currentData() or "rife-v4.25")
        st.rife_gpu_id = int(self.combo_rife_gpu.currentData() if self.combo_rife_gpu.currentData() is not None else 0)
        st.rife_playback_policy = str(self.combo_rife_policy.currentData() or "keep_duration")
        st.custom_fps = self.spin_custom_fps.value()
        st.key_source = "video_frame" if self.rb_key_video.isChecked() else "external_image"
        st.key_frame_index = self.spin_key_frame.value()
        st.external_key_path = self._external_key_path
        st.external_key_aspect_mode = self._selected_ext_aspect_mode()
        st.video_max_edge = self.spin_custom_edge.value()
        st.video_bitrate_mbps = float(self.slider_bitrate.value())
        st.image_format = str(self.combo_img_fmt.currentData() or "heic")
        st.exif_metadata = self._exif_metadata

    def _apply_queue_item(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._queue):
            return
        self._active_idx = idx
        st = self._queue[idx]
        info = st.info

        self._is_playing = False
        self._play_timer.stop()
        self.btn_play.setText("▶ 播放")
        self._video_info = info
        self._frame_provider.load(info)

        self._syncing_ui = True
        try:
            self.lbl_video_info.setText(
                f"{info.path.name} · {info.width}×{info.height} · {info.fps:.0f}fps · {info.nb_frames}帧 ({format_frames_as_sec_frames(info.nb_frames, info.fps)})"
            )
            max_idx = max(0, info.nb_frames - 1)
            self.spin_start.setRange(0, max_idx)
            self.spin_end.setRange(0, max_idx)
            self.spin_key_frame.setRange(0, max_idx)
            self.spin_start.setValue(st.start_frame)
            self.spin_end.setValue(st.end_frame)
            self.spin_key_frame.setValue(st.key_frame_index)

            self._active_aspect_key = st.aspect_key
            if st.aspect_key in self._aspect_buttons:
                self._aspect_buttons[st.aspect_key].setChecked(True)
            self._active_polaroid = st.polaroid_preset
            for k, pb in self._polaroid_buttons.items():
                pb.setChecked(k == st.polaroid_preset)

            if st.audio_mode == "mute":
                self.btn_audio_mute.setChecked(True)
            elif st.audio_mode == "trim_fade":
                self.btn_audio_fade.setChecked(True)
            else:
                self.btn_audio_sync.setChecked(True)

            self._bg_color_hex = st.bg_color
            self.slider_blur.setValue(st.bg_blur_sigma)
            if st.bg_mode == "blur":
                self.btn_bg_blur.setChecked(True)
            elif st.bg_color.upper() == "#FFFFFF":
                self.btn_bg_white.setChecked(True)
            elif st.bg_color.upper() == "#000000":
                self.btn_bg_black.setChecked(True)
            else:
                self.btn_bg_pick.setChecked(True)

            if st.fps_mode == "raw_retime":
                self.rb_retime.setChecked(True)
            elif st.fps_mode in ("rife_interpolate", "ffmpeg_minterpolate"):
                self.rb_rife.setChecked(True)
            else:
                self.rb_keep.setChecked(True)
            self.spin_custom_fps.setValue(st.custom_fps)

            self._external_key_path = st.external_key_path
            self.lbl_ext_name.setText(st.external_key_path.name if st.external_key_path else "未选")
            self.rb_key_video.setChecked(st.key_source == "video_frame")
            self.rb_key_ext.setChecked(st.key_source == "external_image")

            self.spin_custom_edge.setValue(st.video_max_edge)
            for val, eb in self._long_edge_buttons.items():
                eb.setChecked(val == st.video_max_edge)
            self.slider_bitrate.setValue(int(round(st.video_bitrate_mbps)))

            self._exif_metadata = st.exif_metadata
            self.lbl_exif_summary.setText(
                st.exif_metadata.summary_text() if st.exif_metadata else "默认 Apple 实况信息"
            )

            self.btn_crop_toggle.setChecked(False)
            self.preview.set_crop_editing(False)
            self.preview.reset_view()
            self.preview.set_padding(st.padding)
            self.preview.set_crop(st.crop)
            self._refresh_canvas_res_label()
            self.btn_convert.setEnabled(True)
        finally:
            self._syncing_ui = False

        self.timeline.configure(info.nb_frames, st.start_frame, st.end_frame, st.start_frame, st.key_frame_index)
        self._on_bg_changed()
        self._on_fps_mode_changed()
        self._on_key_source_changed()
        self._seek_frame(st.start_frame)
        self._refresh_key_thumbnail()
        self._update_summary()
        self._update_export_preview()
        self._rebuild_queue_bar()

    def _rebuild_queue_bar(self) -> None:
        while self.queue_chips_lay.count() > 0:
            item = self.queue_chips_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        count = len(self._queue)
        self.queue_bar.setVisible(count >= 1)
        self.lbl_queue_title.setText(f"队列 ({count}):")
        self.btn_convert_all.setVisible(count > 1)
        self.btn_queue_sync.setVisible(count > 1)

        for idx, st in enumerate(self._queue):
            chip = ChipButton(f"{idx + 1}. {st.info.path.name}")
            chip.setChecked(idx == self._active_idx)
            chip.clicked.connect(lambda _=False, i=idx: self._switch_queue_item(i))
            self.queue_chips_lay.addWidget(chip)

            if count > 1:
                rm = QPushButton("×")
                rm.setFixedSize(22, 22)
                rm.setStyleSheet("padding: 0; border-radius: 11px;")
                rm.clicked.connect(lambda _=False, i=idx: self._remove_queue_item(i))
                self.queue_chips_lay.addWidget(rm)

        self.queue_chips_lay.addStretch(1)

    def _switch_queue_item(self, idx: int) -> None:
        if idx == self._active_idx or idx < 0 or idx >= len(self._queue):
            return
        self._capture_active_state()
        self._apply_queue_item(idx)

    def _remove_queue_item(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._queue):
            return
        self._queue.pop(idx)
        if not self._queue:
            self._clear_queue()
            return
        new_idx = min(self._active_idx, len(self._queue) - 1)
        self._active_idx = -1
        self._apply_queue_item(new_idx)

    def _clear_queue(self) -> None:
        self._is_playing = False
        self._play_timer.stop()
        self._frame_provider.stop()
        self._queue.clear()
        self._active_idx = -1
        self._video_info = None
        self.preview.set_frame(None, 0, 0, 0)
        self.lbl_video_info.setText("未加载视频 (支持拖入多个 MP4 / MOV)")
        self.btn_convert.setEnabled(False)
        self.queue_bar.setVisible(False)
        self.btn_convert_all.setVisible(False)

    def _sync_current_settings_to_all(self) -> None:
        if not self._queue:
            return
        self._capture_active_state()
        src = self._queue[self._active_idx]
        for idx, st in enumerate(self._queue):
            if idx == self._active_idx:
                continue
            st.audio_mode = src.audio_mode
            st.bg_mode = src.bg_mode
            st.bg_color = src.bg_color
            st.bg_blur_sigma = src.bg_blur_sigma
            st.fps_mode = src.fps_mode
            st.rife_mode = src.rife_mode
            st.rife_multiplier = src.rife_multiplier
            st.rife_model = src.rife_model
            st.rife_gpu_id = src.rife_gpu_id
            st.rife_playback_policy = src.rife_playback_policy
            st.custom_fps = src.custom_fps
            st.video_max_edge = src.video_max_edge
            st.video_bitrate_mbps = src.video_bitrate_mbps
            st.image_format = src.image_format
            st.exif_metadata = src.exif_metadata.clone() if src.exif_metadata else None
            if src.polaroid_preset:
                st.polaroid_preset = src.polaroid_preset
                st.crop, st.padding = compute_polaroid_layout(
                    src.polaroid_preset, st.info.width, st.info.height
                )
        self.lbl_status.setText(f"已将当前参数同步至全部 {len(self._queue)} 个视频")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        videos: list[Path] = []
        exif_files: list[Path] = []
        for url in event.mimeData().urls():
            p = Path(url.toLocalFile())
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            if ext in VIDEO_EXTENSIONS:
                videos.append(p)
            elif ext in SIDECAR_EXIF_EXTENSIONS:
                exif_files.append(p)

        if videos:
            self.load_videos(videos)
        elif exif_files and self._video_info is not None:
            try:
                meta = read_exif_from_file(exif_files[0])
                if not meta.is_empty():
                    self._exif_metadata = meta
                    self.lbl_exif_summary.setText(meta.summary_text())
                    self.lbl_status.setText(f"已从 {exif_files[0].name} 导入 EXIF 参数")
            except Exception as exc:
                QMessageBox.warning(self, "读取 EXIF 失败", str(exc))

    def _on_open_videos(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "导入视频文件 (可多选)",
            str(Path.home() / "Desktop"),
            "视频文件 (*.mp4 *.mov *.mkv *.avi *.m4v *.webm);;所有文件 (*.*)",
        )
        if paths:
            self.load_videos([Path(p) for p in paths])

    def load_video(self, path: Path) -> None:
        self.load_videos([path])

    def load_videos(self, paths: list[Path]) -> None:
        if self._active_idx >= 0:
            self._capture_active_state()

        added_first = -1
        for p in paths:
            existing = next((i for i, item in enumerate(self._queue) if item.info.path == p), None)
            if existing is not None:
                if added_first == -1:
                    added_first = existing
                continue
            try:
                info = probe_video(p)
            except Exception as exc:
                QMessageBox.critical(self, "无法读取视频", f"{p.name}: {exc}")
                continue
            self._queue.append(self._default_item_state(info))
            if added_first == -1:
                added_first = len(self._queue) - 1

        if added_first != -1:
            self._apply_queue_item(added_first)

    # ---------- Playback & Timeline ----------

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
        cur_txt = format_sec_frames(out_sec, summary.output_fps)
        tot_txt = format_frames_as_sec_frames(summary.output_frames, summary.output_fps)
        self.lbl_playhead.setText(
            f"当前: 第 {self._current_frame} 帧  |  当前设定下时刻: {cur_txt} / {tot_txt}"
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

    # ---------- Card 01: Crop, Polaroid, Flat Background ----------

    def _on_crop_toggle_changed(self, checked: bool) -> None:
        self.btn_crop_toggle.setText("✓ 完成裁切" if checked else "✂ 裁切画面")
        self.preview.set_crop_editing(checked)

    def _on_zoom_changed(self, zoom: float) -> None:
        pct = int(round(zoom * 100))
        self.btn_zoom_reset.setText(f"{pct}% 重置视图")

    def _refresh_canvas_res_label(self) -> None:
        if self._video_info is None:
            return
        c = self.preview.get_crop()
        pad = self.preview.get_padding()
        tw = c.w + pad.left + pad.right
        th = c.h + pad.top + pad.bottom
        if self._active_polaroid and not pad.is_zero():
            tag = f" ({POLAROID_PRESETS[self._active_polaroid].label})"
        elif c.is_out_of_bounds(self._video_info.width, self._video_info.height) or not pad.is_zero():
            tag = " (外扩)"
        else:
            tag = ""
        self.lbl_crop_res.setText(f"画布: {tw}×{th}{tag}")

    def _on_crop_changed(self, crop: CropRect) -> None:
        if self._video_info is None:
            return
        self._refresh_canvas_res_label()
        self._refresh_key_thumbnail()
        self._update_size_estimate()

    def _clear_polaroid_selection(self) -> None:
        self._active_polaroid = None
        for pb in self._polaroid_buttons.values():
            pb.setChecked(False)
        self.preview.set_padding(FramePadding())

    def _on_aspect_button_clicked(self, key: str) -> None:
        self._active_aspect_key = key
        self._clear_polaroid_selection()
        fit_mode = "pad" if self.btn_fit_pad.isChecked() else "inside"
        self._reapply_current_aspect(fit_mode)

    def _reapply_current_aspect(self, fit_mode: str) -> None:
        if self._video_info is None:
            return
        self._clear_polaroid_selection()
        ratio = None
        for _lbl, k, r in ASPECT_PRESETS:
            if k == self._active_aspect_key:
                ratio = (self._video_info.width / self._video_info.height) if r is None else (None if r < 0 else r)
                break
        if not self.btn_crop_toggle.isChecked():
            self.btn_crop_toggle.setChecked(True)
        self.preview.apply_aspect_fit(ratio, mode=fit_mode)
        self._refresh_canvas_res_label()
        self._update_size_estimate()

    def _apply_manual_ratio(self) -> None:
        if self._video_info is None:
            return
        txt = self.edit_custom_ratio.text().strip().replace("：", ":")
        if not txt:
            return
        m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*[:/xX]\s*(\d+(?:\.\d+)?)\s*$", txt)
        if m:
            w_val, h_val = float(m.group(1)), float(m.group(2))
            if w_val > 0 and h_val > 0:
                ratio = w_val / h_val
            else:
                return
        else:
            try:
                ratio = float(txt)
            except ValueError:
                QMessageBox.warning(self, "比例格式无效", "请输入如 5:4、16:10 或 1.25 的比例。")
                return
            if ratio <= 0:
                return
        self._clear_polaroid_selection()
        fit_mode = "pad" if self.btn_fit_pad.isChecked() else "inside"
        if not self.btn_crop_toggle.isChecked():
            self.btn_crop_toggle.setChecked(True)
        self.preview.apply_aspect_fit(ratio, mode=fit_mode)
        self._refresh_canvas_res_label()
        self._update_size_estimate()

    def _apply_polaroid_preset(self, preset_key: str) -> None:
        if self._video_info is None:
            return
        self._active_polaroid = preset_key
        for k, pb in self._polaroid_buttons.items():
            pb.setChecked(k == preset_key)

        crop, pad = compute_polaroid_layout(
            preset_key,
            self._video_info.width,
            self._video_info.height,
            current_crop=self.preview.get_crop(),
        )
        # Default instant film border to pure white if blur was active
        if self.btn_bg_blur.isChecked():
            self._set_bg_color("#FFFFFF", self.btn_bg_white)
        self.preview.set_padding(pad)
        self.preview.set_crop(crop, emit=True)
        self._refresh_canvas_res_label()
        self._update_size_estimate()

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
        self._active_aspect_key = "orig"
        self._aspect_buttons["orig"].setChecked(True)
        self._clear_polaroid_selection()
        self.preview.set_crop(CropRect(0, 0, self._video_info.width, self._video_info.height), emit=True)
        self._refresh_canvas_res_label()
        self._update_size_estimate()

    def _on_bg_blur_selected(self) -> None:
        self._on_bg_changed()

    def _on_bg_changed(self) -> None:
        is_blur = self.btn_bg_blur.isChecked()
        self.slider_blur.setVisible(is_blur)
        self.preview.set_background_style(
            "blur" if is_blur else "solid",
            self._bg_color_hex,
            self.slider_blur.value(),
        )

    def _set_bg_color(self, hex_col: str, button: ChipButton | None = None) -> None:
        self._bg_color_hex = hex_col.upper()
        self._settings.setValue("bg_color", self._bg_color_hex)
        if button is not None:
            button.setChecked(True)
        elif self._bg_color_hex == "#FFFFFF":
            self.btn_bg_white.setChecked(True)
        elif self._bg_color_hex == "#000000":
            self.btn_bg_black.setChecked(True)
        else:
            self.btn_bg_pick.setChecked(True)
        self._on_bg_changed()

    def _pick_bg_color(self) -> None:
        c = QColorDialog.getColor(QColor(self._bg_color_hex), self, "选择外扩背景纯色")
        if c.isValid():
            self.btn_bg_pick.setText(f"🎨 {c.name().upper()}")
            self._set_bg_color(c.name().upper(), self.btn_bg_pick)
        else:
            self.btn_bg_blur.setChecked(True)
            self._on_bg_changed()

    # ---------- Card 02: FPS, RIFE 30fps default, GPU, 3s Trim ----------

    def _populate_rife_models(self) -> None:
        self.combo_rife_model.clear()
        models = list_available_rife_models()
        if not models:
            self.combo_rife_model.addItem("FFmpeg 光流回退", "ffmpeg_minterpolate")
        else:
            for m in models:
                tag = " (高画质)" if "4.25" in m else (" (极速)" if "4.6" in m else "")
                self.combo_rife_model.addItem(f"{m}{tag}", m)

    def _populate_rife_gpus(self) -> None:
        self.combo_rife_gpu.clear()
        if self._vulkan_gpus:
            for gid, name in self._vulkan_gpus:
                self.combo_rife_gpu.addItem(f"GPU {gid}: {name} (Vulkan)", gid)
        else:
            self.combo_rife_gpu.addItem("自动 Vulkan GPU", 0)
        self.combo_rife_gpu.addItem("CPU 软件回退", -1)

    def _on_fps_mode_changed(self) -> None:
        is_retime = self.rb_retime.isChecked()
        is_rife = self.rb_rife.isChecked()
        self.rife_panel.setVisible(is_rife)

        rm_data = self.combo_rife_mode.currentData() or ("target_fps", 1)
        is_mult = is_rife and rm_data[0] == "multiplier"
        self.rife_policy_row.setVisible(is_mult)

        show_custom_fps = is_retime or (is_rife and not is_mult) or (is_mult and self.combo_rife_policy.currentData() == "custom_fps")
        self.custom_fps_panel.setVisible(show_custom_fps)
        self._update_summary()

    def _on_custom_fps_changed(self, val: float) -> None:
        for preset_val, btn in self._fps_chip_buttons:
            btn.setChecked(abs(val - preset_val) < 0.01)
        self._update_summary()

    def _fit_duration_under_3s(self) -> None:
        """Trim [start_frame, end_frame] to 3s around Key Photo WITHOUT altering FPS."""
        if self._video_info is None:
            return
        job = self._build_job()
        new_start, new_end = compute_3s_trim_around_key(self._video_info, job)
        self._syncing_ui = True
        try:
            self.spin_start.setValue(new_start)
            self.spin_end.setValue(new_end)
            kf = max(new_start, min(self.spin_key_frame.value(), new_end))
            self.spin_key_frame.setValue(kf)
            self.timeline.configure(self._video_info.nb_frames, new_start, new_end, self._current_frame, kf)
        finally:
            self._syncing_ui = False
        self._update_summary()

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

    # ---------- Card 03: Key Photo & Aspect Mismatch ----------

    def _selected_ext_aspect_mode(self) -> str:
        if self.btn_ext_pad.isChecked():
            return "pad_fit"
        if self.btn_ext_match.isChecked():
            return "match_cover_aspect"
        return "crop_fill"

    def _on_ext_aspect_mode_clicked(self, mode_val: str) -> None:
        if mode_val == "match_cover_aspect" and self._external_key_path and self._external_key_path.is_file() and self._video_info is not None:
            try:
                with Image.open(self._external_key_path) as im:
                    ratio = im.width / max(1, im.height)
                self._clear_polaroid_selection()
                if not self.btn_crop_toggle.isChecked():
                    self.btn_crop_toggle.setChecked(True)
                self.preview.apply_aspect_fit(ratio, mode="inside")
                self._refresh_canvas_res_label()
            except Exception:
                pass
        self._update_summary()

    def _on_key_source_changed(self) -> None:
        is_video = self.rb_key_video.isChecked()
        self.spin_key_frame.setEnabled(is_video)
        self.btn_take_cur_key.setEnabled(is_video)
        self.btn_choose_ext.setEnabled(not is_video)
        self.ext_aspect_panel.setVisible(not is_video)
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
            "图片文件 (*.jpg *.jpeg *.png *.heic *.heif *.webp *.bmp *.tif *.tiff)",
        )
        if path_str:
            self._external_key_path = Path(path_str)
            self.lbl_ext_name.setText(self._external_key_path.name)
            self.rb_key_ext.setChecked(True)
            self._refresh_key_thumbnail()
            if self.btn_ext_match.isChecked():
                self._on_ext_aspect_mode_clicked("match_cover_aspect")

    def _refresh_key_thumbnail(self) -> None:
        if self.rb_key_ext.isChecked() and self._external_key_path and self._external_key_path.is_file():
            pix = QPixmap(str(self._external_key_path))
            if not pix.isNull():
                self.lbl_key_thumb.setPixmap(
                    pix.scaled(64, 70, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
                return
        if self._video_info is not None:
            qimg = self._frame_provider.get_frame(self.spin_key_frame.value())
            if qimg is not None:
                pix = QPixmap.fromImage(qimg)
                self.lbl_key_thumb.setPixmap(
                    pix.scaled(64, 70, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
                return
        self.lbl_key_thumb.setText("封面")

    # ---------- Card 04: Long Edge, Bitrate, EXIF, USB ----------

    def _on_long_edge_preset_clicked(self, val: int) -> None:
        self._syncing_ui = True
        try:
            self.spin_custom_edge.setValue(val)
            for k, eb in self._long_edge_buttons.items():
                eb.setChecked(k == val)
        finally:
            self._syncing_ui = False
        self._update_size_estimate()

    def _on_custom_edge_changed(self, val: int) -> None:
        if self._syncing_ui:
            return
        for k, eb in self._long_edge_buttons.items():
            eb.setChecked(k == val)
        self._update_size_estimate()

    def _on_bitrate_changed(self, val: int) -> None:
        self.lbl_bitrate_val.setText(f"{val} Mbps")
        self._update_size_estimate()

    def _on_export_param_changed(self) -> None:
        self._update_export_preview()
        self._update_size_estimate()

    def _on_open_exif_dialog(self) -> None:
        dlg = ExifEditorDialog(
            initial=self._exif_metadata,
            video_path=self._video_info.path if self._video_info else None,
            parent=self,
        )
        if dlg.exec() == ExifEditorDialog.DialogCode.Accepted:
            self._exif_metadata = dlg.get_exif_metadata()
            self.lbl_exif_summary.setText(
                self._exif_metadata.summary_text() if self._exif_metadata else "默认 Apple 实况信息"
            )

    def _on_quick_auto_exif(self) -> None:
        if self._video_info is None:
            return
        match = find_matching_sidecar_exif_file(self._video_info.path)
        if match is None:
            QMessageBox.information(self, "未找到同名 RAW/照片", f"未在 {self._video_info.path.parent} 找到同名照片或 RAW，请点击「修改 / 导入 EXIF...」手动选择。")
            return
        try:
            meta = read_exif_from_file(match)
        except Exception as exc:
            QMessageBox.warning(self, "读取 EXIF 失败", str(exc))
            return
        self._exif_metadata = None if meta.is_empty() else meta
        self.lbl_exif_summary.setText(meta.summary_text())
        self.lbl_status.setText(f"已匹配导入 EXIF: {match.name}")

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

    # ---------- Job Builder & Summary ----------

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

        rm_data = self.combo_rife_mode.currentData() or ("target_fps", 1)
        rife_mode = rm_data[0]
        rife_mult = int(rm_data[1])

        audio_retime_mode = "trim_fade" if self.btn_audio_fade.isChecked() else "speed_sync"
        is_usb = self.rb_export_usb.isChecked()
        usb_data = str(self.combo_usb.currentData() or "")
        out_root = Path(usb_data) if is_usb else Path(self.edit_folder.text().strip())

        return ConversionJob(
            video_path=self._video_info.path,
            start_frame=self.spin_start.value(),
            end_frame=self.spin_end.value(),
            crop=self.preview.get_crop(),
            padding=self.preview.get_padding(),
            polaroid_preset=self._active_polaroid,
            mute=self.btn_audio_mute.isChecked(),
            fps_mode=fps_mode,  # type: ignore[arg-type]
            custom_fps=self.spin_custom_fps.value(),
            rife_mode=rife_mode,  # type: ignore[arg-type]
            rife_multiplier=rife_mult,
            rife_model=str(self.combo_rife_model.currentData() or "rife-v4.25"),
            rife_gpu_id=int(self.combo_rife_gpu.currentData() if self.combo_rife_gpu.currentData() is not None else 0),
            rife_playback_policy=str(self.combo_rife_policy.currentData() or "keep_duration"),  # type: ignore[arg-type]
            audio_retime_mode=audio_retime_mode,  # type: ignore[arg-type]
            key_source="video_frame" if self.rb_key_video.isChecked() else "external_image",
            key_frame_index=self.spin_key_frame.value(),
            external_key_image=self._external_key_path,
            external_key_aspect_mode=self._selected_ext_aspect_mode(),  # type: ignore[arg-type]
            export_mode="usb_dcim" if is_usb else "folder",
            output_root=out_root,
            dcf_prefix=normalize_dcf_prefix(self.edit_dcf_prefix.text()),
            dcf_number=self.spin_dcf.value(),
            also_pack_livp=self.chk_livp.isChecked(),
            image_format=str(self.combo_img_fmt.currentData() or "heic"),  # type: ignore[arg-type]
            video_max_edge=self.spin_custom_edge.value(),
            video_bitrate_mbps=float(self.slider_bitrate.value()),
            use_hw_encode=self.chk_hw_encode.isChecked(),
            bg_mode="blur" if self.btn_bg_blur.isChecked() else "solid",
            bg_blur_sigma=self.slider_blur.value(),
            bg_color=self._bg_color_hex,
            exif_metadata=self._exif_metadata,
        )

    def _build_job_for_state(self, st: QueueItemState, dcf_num: int) -> ConversionJob:
        is_usb = self.rb_export_usb.isChecked()
        usb_data = str(self.combo_usb.currentData() or "")
        out_root = Path(usb_data) if is_usb else Path(self.edit_folder.text().strip())
        fps_mode = st.fps_mode
        if fps_mode == "rife_interpolate" and (st.rife_model == "ffmpeg_minterpolate" or find_rife() is None):
            fps_mode = "ffmpeg_minterpolate"
        return ConversionJob(
            video_path=st.info.path,
            start_frame=st.start_frame,
            end_frame=st.end_frame,
            crop=st.crop,
            padding=st.padding,
            polaroid_preset=st.polaroid_preset,
            mute=(st.audio_mode == "mute"),
            fps_mode=fps_mode,  # type: ignore[arg-type]
            custom_fps=st.custom_fps,
            rife_mode=st.rife_mode,  # type: ignore[arg-type]
            rife_multiplier=st.rife_multiplier,
            rife_model=st.rife_model,
            rife_gpu_id=st.rife_gpu_id,
            rife_playback_policy=st.rife_playback_policy,  # type: ignore[arg-type]
            audio_retime_mode="trim_fade" if st.audio_mode == "trim_fade" else "speed_sync",
            key_source=st.key_source,  # type: ignore[arg-type]
            key_frame_index=st.key_frame_index,
            external_key_image=st.external_key_path,
            external_key_aspect_mode=st.external_key_aspect_mode,  # type: ignore[arg-type]
            export_mode="usb_dcim" if is_usb else "folder",
            output_root=out_root,
            dcf_prefix=normalize_dcf_prefix(self.edit_dcf_prefix.text()),
            dcf_number=dcf_num,
            also_pack_livp=self.chk_livp.isChecked(),
            image_format=st.image_format,  # type: ignore[arg-type]
            video_max_edge=st.video_max_edge,
            video_bitrate_mbps=st.video_bitrate_mbps,
            use_hw_encode=self.chk_hw_encode.isChecked(),
            bg_mode=st.bg_mode,  # type: ignore[arg-type]
            bg_blur_sigma=st.bg_blur_sigma,
            bg_color=st.bg_color,
            exif_metadata=st.exif_metadata,
        )

    def _update_size_estimate(self) -> None:
        if self._video_info is None:
            self.lbl_est_size.setText("预计: -")
            return
        job = self._build_job()
        summary = compute_timeline_summary(self._video_info, job)
        img_mb, mov_mb, tot_mb = estimate_output_size_mb(self._video_info, job, summary)
        self.lbl_est_size.setText(f"预计 ~{tot_mb:.1f} MB (图 {img_mb:.1f} + 影 {mov_mb:.1f})")

    def _update_summary(self) -> None:
        if self._video_info is None:
            return
        job = self._build_job()
        s = compute_timeline_summary(self._video_info, job)
        self.timeline.set_timing_context(s.output_duration, s.cover_still_time, s.output_fps, s.output_frames)
        self._update_playhead_label()
        self._update_size_estimate()

        dur = s.output_duration
        self.btn_fit_3s.setVisible(dur > 3.0)
        if dur > 3.0:
            bg = PALETTE["sakura"]
            bd = PALETTE["sakura_border"]
            fg = PALETTE["sakura_ink"]
            note = "\n⚠ 实况超过 3秒0帧，建议点击「✂ 控时3秒」在主图附近裁切"
        elif dur < 1.0:
            bg = PALETTE["sakura"]
            bd = PALETTE["sakura_border"]
            fg = PALETTE["sakura_ink"]
            note = "\n⚠ 时长短于 1秒0帧，建议降低帧率或开启 AI 补帧"
        else:
            bg = PALETTE["mint_soft"]
            bd = PALETTE["sky"]
            fg = PALETTE["ink_primary"]
            note = ""

        in_dur_txt = format_frames_as_sec_frames(s.selected_frames, self._video_info.fps)
        out_dur_txt = format_frames_as_sec_frames(s.output_frames, s.output_fps)
        cover_txt = format_sec_frames(s.cover_still_time, s.output_fps)
        fps_int = int(round(s.output_fps))

        self.lbl_summary.setStyleSheet(
            f"background-color: {bg}; border: 1px solid {bd}; border-radius: 8px; padding: 6px 10px; color: {fg};"
        )
        self.lbl_summary.setText(
            f"截取: {s.selected_frames} 帧 ({in_dur_txt})  ➔  输出: {s.output_frames} 帧 @ {fps_int} 帧/秒\n"
            f"实况时长: {out_dur_txt}  |  封面锚点: {cover_txt}{note}"
        )

    # ---------- Context Menu & Shortcuts ----------

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
        backdrop_menu.addAction("瓷白画布 (#FEFDF9, 默认)").triggered.connect(
            lambda: self._set_backdrop(PALETTE["canvas_porcelain"])
        )
        backdrop_menu.addAction("柔和浅蓝 (#E6ECF8)").triggered.connect(
            lambda: self._set_backdrop(PALETTE["canvas_light"])
        )
        backdrop_menu.addAction("专业深色暗房 (#1E2130)").triggered.connect(
            lambda: self._set_backdrop(PALETTE["canvas_dark"])
        )

        pol_menu = menu.addMenu("🎞 拍立得相纸")
        for k, spec in POLAROID_PRESETS.items():
            pol_menu.addAction(spec.label).triggered.connect(lambda _=False, pk=k: self._apply_polaroid_preset(pk))
        pol_menu.addSeparator()
        pol_menu.addAction("重置为全画面").triggered.connect(self._reset_crop)

        bg_menu = menu.addMenu("🖼 外扩背景")
        bg_menu.addAction("高斯模糊").triggered.connect(lambda: self.btn_bg_blur.click())
        bg_menu.addAction("纯白 (#FFFFFF)").triggered.connect(lambda: self._set_bg_color("#FFFFFF", self.btn_bg_white))
        bg_menu.addAction("纯黑 (#000000)").triggered.connect(lambda: self._set_bg_color("#000000", self.btn_bg_black))
        bg_menu.addAction("自定义取色...").triggered.connect(self._pick_bg_color)

        menu.addSeparator()
        menu.addAction("★ 设当前帧为实况封面 (K)").triggered.connect(self._set_current_as_key)
        menu.addAction("[ 设为片段起点").triggered.connect(self._set_current_as_start)
        menu.addAction("] 设为片段终点").triggered.connect(self._set_current_as_end)

        if self._video_info is not None:
            menu.addSeparator()
            menu.addAction("💾 导出当前帧为单张图片...").triggered.connect(self._export_current_frame_image)

        menu.exec(global_pos)

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

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self._toggle_play)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self._seek_frame(self._current_frame - 1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self._seek_frame(self._current_frame + 1))
        QShortcut(QKeySequence("Shift+Left"), self, lambda: self._seek_frame(self._current_frame - 5))
        QShortcut(QKeySequence("Shift+Right"), self, lambda: self._seek_frame(self._current_frame + 5))
        QShortcut(QKeySequence("["), self, self._set_current_as_start)
        QShortcut(QKeySequence("]"), self, self._set_current_as_end)
        QShortcut(QKeySequence("K"), self, self._set_current_as_key)

    # ---------- Conversion Execution ----------

    def _validate_export_target(self) -> bool:
        if self.rb_export_usb.isChecked() and not str(self.combo_usb.currentData() or "").strip():
            QMessageBox.warning(self, "未选择 U 盘", "请插入 U 盘后点击「刷新U盘」，或点击「自选目录...」指定目标目录。")
            return False
        if self.rb_key_ext.isChecked() and (self._external_key_path is None or not self._external_key_path.is_file()):
            QMessageBox.warning(self, "缺少主要图片", "您选择了外部自定义图片，请先选择一张图片文件。")
            return False
        return True

    def _on_start_convert(self) -> None:
        if self._video_info is None or not self._validate_export_target():
            return
        job = self._build_job()
        self.btn_convert.setEnabled(False)
        self.btn_convert_all.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_open_out.setEnabled(False)
        self.progress_bar.setValue(0)
        self.lbl_status.setText("正在启动转换任务...")

        worker = ConversionWorker(job)
        worker.progress.connect(self._on_worker_progress)
        worker.succeeded.connect(self._on_worker_succeeded)
        worker.cancelled.connect(self._on_worker_cancelled)
        worker.failed.connect(self._on_worker_failed)
        self._worker = worker
        worker.start()

    def _on_start_batch_convert(self) -> None:
        if not self._queue or not self._validate_export_target():
            return
        self._capture_active_state()
        dcf_seed = self.spin_dcf.value()
        jobs = [self._build_job_for_state(st, dcf_seed) for st in self._queue]

        self.btn_convert.setEnabled(False)
        self.btn_convert_all.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_open_out.setEnabled(False)
        self.progress_bar.setValue(0)
        self.lbl_status.setText(f"正在启动批量转换 ({len(jobs)} 个视频)...")

        worker = BatchConversionWorker(jobs, auto_inc_dcf=self.chk_dcf_auto_inc.isChecked())
        worker.progress.connect(self._on_worker_progress)
        worker.allSucceeded.connect(self._on_batch_succeeded)
        worker.cancelled.connect(self._on_worker_cancelled)
        worker.failed.connect(self._on_worker_failed)
        self._worker = worker
        worker.start()

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
        self.btn_convert_all.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_open_out.setEnabled(True)
        self.progress_bar.setValue(100)

        if result.used_dcf_number is not None and self.chk_dcf_auto_inc.isChecked():
            next_num = (result.used_dcf_number % 9999) + 1
            self.spin_dcf.setValue(next_num)

        dur_txt = format_frames_as_sec_frames(result.summary.output_frames, result.summary.output_fps)
        self.lbl_status.setText(
            f"生成成功！{result.jpg_path.name} + {result.mov_path.name} (UUID: {result.asset_id[:8]}...)"
        )
        QMessageBox.information(
            self,
            "实况照片生成成功",
            f"已导出配对实况照片:\n{result.jpg_path}\n{result.mov_path}\n\n"
            f"输出规格: {result.summary.output_frames} 帧 @ {int(round(result.summary.output_fps))} 帧/秒 ({dur_txt})",
        )

    def _on_batch_succeeded(self, results: list[ConversionResult]) -> None:
        self.btn_convert.setEnabled(True)
        self.btn_convert_all.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress_bar.setValue(100)
        if results:
            self._last_result = results[-1]
            self.btn_open_out.setEnabled(True)
            last_num = results[-1].used_dcf_number
            if last_num is not None and self.chk_dcf_auto_inc.isChecked():
                self.spin_dcf.setValue((last_num % 9999) + 1)
        self.lbl_status.setText(f"批量生成完成！共导出 {len(results)} 组实况照片。")
        QMessageBox.information(
            self,
            "批量实况生成成功",
            f"已成功导出 {len(results)} 组配对实况照片到目标目录。",
        )

    def _on_worker_cancelled(self) -> None:
        self.btn_convert.setEnabled(True)
        self.btn_convert_all.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress_bar.setValue(0)
        self.lbl_status.setText("已取消转换")

    def _on_worker_failed(self, err: str) -> None:
        self.btn_convert.setEnabled(True)
        self.btn_convert_all.setEnabled(True)
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
