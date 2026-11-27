"""Main PySide6 desktop studio window for ToLivePhoto v2.3 (Streamlined Crop, Dual Info Bar, Naming & Fixed Simple Bitrate)."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QElapsedTimer, QPoint, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QDoubleValidator,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QMouseEvent,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QColorDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
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
    IPHONE_LIVE_BITRATE_MBPS,
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
    preview_output_filename,
)
from ..core.pipeline import ConversionResult, compute_scaled_video_dims
from ..core.probe import probe_video
from ..core.tools import find_rife, has_nvenc, list_available_rife_models, list_vulkan_gpus
from ..core.usb_export import list_usb_drives
from .crop_preview_widget import CropPreviewWidget
from .exif_dialog import ExifEditorDialog
from .frame_provider import PreviewFrameProvider
from .icon import create_app_icon
from .range_slider import RangeTimelineSlider
from .theme import PALETTE, build_pastel_stylesheet, compute_dpi_font_scale, ensure_bytedance_font
from .widgets import (
    ChipButton,
    ElidedLabel,
    FourDigitSpinBox,
    NoWheelComboBox,
    NoWheelDoubleSpinBox,
    NoWheelSlider,
    NoWheelSpinBox,
    SegmentedBar,
    StyledCheckBox,
)
from .worker import BatchConversionWorker, ConversionWorker, RifeDownloadWorker

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}

CANONICAL_RATIOS: list[tuple[str, str, float | None]] = [
    ("自由", "free", -1.0),
    ("原比例", "orig", None),
    ("1:1", "1:1", 1.0),
    ("2:3", "2:3", 2.0 / 3.0),
    ("3:4", "3:4", 3.0 / 4.0),
    ("9:16", "9:16", 9.0 / 16.0),
]

FPS_PRESET_VALUES = (24, 25, 30, 60)
LONG_EDGE_PRESETS = (0, 2160, 1440, 1080, 720)


@dataclass(slots=True)
class QueueItemState:
    info: VideoInfo
    thumb_pixmap: QPixmap | None
    start_frame: int
    end_frame: int
    crop: CropRect
    padding: FramePadding
    framing_mode: str  # "none" | "crop"
    polaroid_preset: str | None
    aspect_key: str
    custom_ratio: float | None
    orientation: str   # "portrait" | "landscape"
    audio_mode: str    # "speed_sync" | "trim_fade" | "mute"
    bg_mode: str       # "blur" | "solid"
    bg_color: str
    bg_blur_sigma: int
    fps_mode: str      # "keep_original" | "raw_retime" | "rife_interpolate"
    rife_mode: str     # "target_fps" | "multiplier"
    rife_multiplier: int
    rife_model: str
    rife_gpu_id: int
    rife_playback_policy: str
    custom_fps: float
    key_source: str    # "video_frame" | "external_image"
    key_frame_index: int
    external_key_path: Path | None
    external_key_aspect_mode: str
    video_max_edge: int
    video_bitrate_mbps: float
    image_format: str
    exif_metadata: ExifMetadata | None


class QueueThumbCard(QFrame):
    """Clickable video queue item with 48x40 thumbnail, name, specs, and remove button."""

    clicked = Signal(int)
    removeRequested = Signal(int)

    def __init__(
        self,
        idx: int,
        title: str,
        subtitle: str,
        thumb: QPixmap | None,
        active: bool,
        show_remove: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._idx = idx
        self.setObjectName("thumbQueueCardActive" if active else "thumbQueueCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(52)
        self.setMinimumWidth(168)
        self.setMaximumWidth(230)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(6)

        lbl_img = QLabel()
        lbl_img.setFixedSize(48, 40)
        lbl_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_img.setStyleSheet(
            f"background-color: {PALETTE['canvas_porcelain']}; border: 1px solid {PALETTE['border_soft']}; border-radius: 5px;"
        )
        if thumb is not None and not thumb.isNull():
            lbl_img.setPixmap(
                thumb.scaled(46, 38, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
        else:
            lbl_img.setText("🎬")
        lay.addWidget(lbl_img)

        txt_col = QVBoxLayout()
        txt_col.setContentsMargins(0, 0, 0, 0)
        txt_col.setSpacing(1)
        lbl_t = ElidedLabel(f"{idx + 1}. {title}", Qt.TextElideMode.ElideMiddle)
        lbl_t.setStyleSheet(f"color: {PALETTE['ink_primary']};")
        lbl_s = ElidedLabel(subtitle, Qt.TextElideMode.ElideRight)
        lbl_s.setStyleSheet(f"color: {PALETTE['ink_muted']}; font-size: 11px;")
        txt_col.addWidget(lbl_t)
        txt_col.addWidget(lbl_s)
        lay.addLayout(txt_col, 1)

        if show_remove:
            rm = QPushButton("×")
            rm.setFixedSize(20, 20)
            rm.setStyleSheet("padding: 0; border-radius: 10px;")
            rm.clicked.connect(lambda _=False: self.removeRequested.emit(self._idx))
            lay.addWidget(rm)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._idx)
        super().mousePressEvent(event)


def _field_lbl(text: str, width: int = 68) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("fieldLabel")
    lbl.setFixedWidth(width)
    return lbl


def _section_header(title: str) -> QLabel:
    lbl = QLabel(title)
    lbl.setObjectName("subSectionHeader")
    return lbl


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("sectionDivider")
    return line


def _v_divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.VLine)
    line.setStyleSheet(f"color: {PALETTE['border_soft']};")
    return line


class MainWindow(QMainWindow):
    def __init__(self, initial_video: Path | None = None) -> None:
        super().__init__()
        app = QApplication.instance()
        if app is not None:
            app.setStyle("Fusion")
        font_family = ensure_bytedance_font(app)
        scale = compute_dpi_font_scale(app)

        self.setWindowTitle("ToLivePhoto — 视频转 iPhone 实况照片工作台")
        self.setWindowIcon(create_app_icon())
        self.resize(1540, 940)
        self.setAcceptDrops(True)
        self.setStyleSheet(build_pastel_stylesheet(scale, font_family))

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
        self._rife_models = list_available_rife_models()

        self._ui_mode = "simple"
        self._framing_mode = "none"
        self._active_aspect_key = "orig"
        self._custom_ratio: float | None = None
        self._orientation = "portrait"
        self._active_polaroid: str | None = None
        self._selected_rife_mode = "target_fps"
        self._selected_rife_mult = 2
        default_rife = "rife-v4.6" if "rife-v4.6" in self._rife_models else (
            self._rife_models[0] if self._rife_models else "ffmpeg_minterpolate"
        )
        self._selected_rife_model = default_rife
        self._selected_rife_gpu = self._vulkan_gpus[0][0] if self._vulkan_gpus else 0

        self._fps_chip_buttons: list[tuple[int, QPushButton]] = []
        self._aspect_buttons: dict[str, ChipButton] = {}
        self._polaroid_buttons: dict[str, ChipButton] = {}
        self._long_edge_buttons: dict[int, ChipButton] = {}
        self._rife_mode_buttons: dict[tuple[str, int], ChipButton] = {}
        self._rife_model_buttons: dict[str, ChipButton] = {}
        self._rife_gpu_buttons: dict[int, ChipButton] = {}

        self._build_ui()
        self._bind_shortcuts()
        self._refresh_usb_drives()
        self.set_ui_mode("simple")

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
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # ---- RESIZABLE MAIN SPLITTER ----
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(14)
        outer.addWidget(self.main_splitter, 1)

        # ---- LEFT STAGE ----
        stage_card = QFrame()
        stage_card.setObjectName("stageCard")
        stage_card.setMinimumWidth(420)
        left_layout = QVBoxLayout(stage_card)
        left_layout.setContentsMargins(10, 8, 10, 8)
        left_layout.setSpacing(6)

        stage_top = QHBoxLayout()
        stage_top.setSpacing(6)
        self.lbl_crop_res = QLabel("画布: -")
        self.lbl_crop_res.setObjectName("metaCapsule")
        self.btn_compare_orig = QPushButton("👁 按住对比原片")
        self.btn_compare_orig.setToolTip("按住鼠标左键查看未裁切/未加边框的原始画面")
        self.btn_compare_orig.pressed.connect(lambda: self.preview.set_compare_original(True))
        self.btn_compare_orig.released.connect(lambda: self.preview.set_compare_original(False))

        self.btn_zoom_out = QPushButton("−")
        self.btn_zoom_out.setFixedWidth(30)
        self.btn_zoom_out.clicked.connect(lambda: self.preview.set_zoom(self.preview.get_zoom() / 1.25))
        self.btn_zoom_reset = QPushButton("100% 适应")
        self.btn_zoom_reset.clicked.connect(lambda: self.preview.reset_view())
        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_in.setFixedWidth(30)
        self.btn_zoom_in.clicked.connect(lambda: self.preview.set_zoom(self.preview.get_zoom() * 1.25))

        stage_top.addWidget(self.lbl_crop_res)
        stage_top.addWidget(self.btn_compare_orig)
        stage_top.addStretch(1)
        stage_top.addWidget(self.btn_zoom_out)
        stage_top.addWidget(self.btn_zoom_reset)
        stage_top.addWidget(self.btn_zoom_in)
        left_layout.addLayout(stage_top)

        self.preview = CropPreviewWidget()
        self.preview.set_backdrop_color(self._backdrop_hex)
        self.preview.cropChanged.connect(self._on_crop_changed)
        self.preview.cropEditingChanged.connect(self._on_preview_crop_editing_changed)
        self.preview.zoomChanged.connect(self._on_zoom_changed)
        self.preview.contextMenuRequested.connect(self._on_preview_context_menu)
        self.preview.setToolTip("Space 播放 · ←/→ 逐帧 · [ / ] 入出点 · K 设主图 · 双击画布缩放 · 滚轮缩放 · 左键拖拽")
        left_layout.addWidget(self.preview, 1)

        # 3-Cluster Transport Bar (compact for narrow windows)
        transport = QHBoxLayout()
        transport.setSpacing(4)
        self.btn_set_in = QPushButton("[ 起点")
        self.btn_set_in.setToolTip("将当前帧设为片段起点 ([)")
        self.btn_set_out = QPushButton("终点 ]")
        self.btn_set_out.setToolTip("将当前帧设为片段终点 (])")
        self.btn_set_in.clicked.connect(self._set_current_as_start)
        self.btn_set_out.clicked.connect(self._set_current_as_end)
        transport.addWidget(self.btn_set_in)
        transport.addWidget(self.btn_set_out)
        transport.addStretch(1)

        self.btn_jump_in = QPushButton("⏮")
        self.btn_jump_in.setToolTip("跳至片段起点")
        self.btn_prev_frame = QPushButton("◀ -1帧")
        self.btn_play = QPushButton("▶ 播放")
        self.btn_play.setObjectName("heroPlayBtn")
        self.btn_play.setMinimumWidth(78)
        self.btn_next_frame = QPushButton("+1帧 ▶")
        self.btn_jump_out = QPushButton("⏭")
        self.btn_jump_out.setToolTip("跳至片段终点")

        self.btn_jump_in.clicked.connect(lambda: self._seek_frame(self.spin_start.value()))
        self.btn_prev_frame.clicked.connect(lambda: self._seek_frame(self._current_frame - 1))
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_next_frame.clicked.connect(lambda: self._seek_frame(self._current_frame + 1))
        self.btn_jump_out.clicked.connect(lambda: self._seek_frame(self.spin_end.value()))

        for b in (self.btn_jump_in, self.btn_prev_frame, self.btn_play, self.btn_next_frame, self.btn_jump_out):
            transport.addWidget(b)
        transport.addStretch(1)

        self.btn_set_key = QPushButton("★ 设主图")
        self.btn_set_key.setToolTip("将当前帧设为实况封面主图 (K)")
        self.btn_set_key.setObjectName("keyStarBtn")
        self.btn_set_key.clicked.connect(self._set_current_as_key)
        transport.addWidget(self.btn_set_key)
        left_layout.addLayout(transport)

        self.timeline = RangeTimelineSlider()
        self.timeline.rangeChanged.connect(self._on_slider_range_changed)
        self.timeline.currentFrameChanged.connect(self._seek_frame)
        self.timeline.keyPillClicked.connect(lambda: self._seek_frame(self.spin_key_frame.value()))
        left_layout.addWidget(self.timeline)

        playhead_row = QHBoxLayout()
        self.lbl_playhead = ElidedLabel("当前: 第 0 帧  |  设定时刻: 0秒0帧 / 0秒0帧", Qt.TextElideMode.ElideRight)
        self.lbl_playhead.setStyleSheet(f"color: {PALETTE['ink_secondary']};")
        self.lbl_playhead.setToolTip("Space 播放 · ←/→ 逐帧 · [ / ] 入出点 · K 设主图 · 双击画布缩放")
        playhead_row.addWidget(self.lbl_playhead, 1)
        left_layout.addLayout(playhead_row)

        # ---- MULTI-VIDEO QUEUE BAR (ABOVE VIDEO INFO BAR) ----
        self.queue_bar = QFrame()
        self.queue_bar.setObjectName("queueBar")
        qb_lay = QHBoxLayout(self.queue_bar)
        qb_lay.setContentsMargins(6, 4, 6, 4)
        qb_lay.setSpacing(6)

        self.queue_scroll = QScrollArea()
        self.queue_scroll.setWidgetResizable(True)
        self.queue_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.queue_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.queue_scroll.setFixedHeight(54)
        self.queue_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.queue_chips_host = QWidget()
        self.queue_chips_host.setObjectName("queueChipsHost")
        self.queue_chips_lay = QHBoxLayout(self.queue_chips_host)
        self.queue_chips_lay.setContentsMargins(0, 2, 0, 2)
        self.queue_chips_lay.setSpacing(6)
        self.queue_scroll.setWidget(self.queue_chips_host)
        qb_lay.addWidget(self.queue_scroll, 1)

        self.btn_open = QPushButton("📂 导入视频")
        self.btn_open.setToolTip("导入一个或多个视频 (支持拖放)")
        self.btn_open.setObjectName("cropToggleBtn")
        self.btn_open.clicked.connect(self._on_open_videos)
        self.btn_add_queue = self.btn_open
        self.btn_queue_sync = QPushButton("⇄ 同步")
        self.btn_queue_sync.setToolTip("将当前视频参数同步到队列所有视频")
        self.btn_queue_sync.clicked.connect(self._sync_current_settings_to_all)
        self.btn_queue_sync.setVisible(False)
        self.btn_queue_clear = QPushButton("清空")
        self.btn_queue_clear.clicked.connect(self._clear_queue)
        self.btn_queue_clear.setVisible(False)
        qb_lay.addWidget(self.btn_open)
        qb_lay.addWidget(self.btn_queue_sync)
        qb_lay.addWidget(self.btn_queue_clear)
        left_layout.addWidget(self.queue_bar)

        # ---- BOTTOM DUAL VIDEO INFO BAR (SOURCE LEFT + OUTPUT RIGHT) ----
        self.video_info_bar = QFrame()
        self.video_info_bar.setObjectName("videoInfoBar")
        vib_lay = QHBoxLayout(self.video_info_bar)
        vib_lay.setContentsMargins(10, 6, 10, 6)
        vib_lay.setSpacing(8)

        src_col = QVBoxLayout()
        src_col.setSpacing(2)
        self.lbl_full_dir_info = ElidedLabel("源目录: 未加载视频 (支持拖入多个 MP4 / MOV 文件)", Qt.TextElideMode.ElideMiddle)
        self.lbl_full_dir_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_full_dir_info.setStyleSheet(f"color: {PALETTE['ink_primary']};")
        self.lbl_video_info = ElidedLabel("源规格: -", Qt.TextElideMode.ElideRight)
        self.lbl_video_info.setStyleSheet(f"color: {PALETTE['ink_secondary']};")
        src_col.addWidget(self.lbl_full_dir_info)
        src_col.addWidget(self.lbl_video_info)
        vib_lay.addLayout(src_col, 5)

        vib_lay.addWidget(_v_divider())

        out_col = QVBoxLayout()
        out_col.setSpacing(2)
        self.lbl_out_file_info = ElidedLabel("输出文件: -", Qt.TextElideMode.ElideMiddle)
        self.lbl_out_file_info.setStyleSheet(f"color: {PALETTE['indigo_pressed']};")
        self.lbl_out_spec_info = ElidedLabel("输出规格: -", Qt.TextElideMode.ElideRight)
        self.lbl_out_spec_info.setStyleSheet(f"color: {PALETTE['ink_secondary']};")
        out_col.addWidget(self.lbl_out_file_info)
        out_col.addWidget(self.lbl_out_spec_info)
        vib_lay.addLayout(out_col, 4)

        left_layout.addWidget(self.video_info_bar)

        self.main_splitter.addWidget(stage_card)

        # ---- RIGHT UNIFIED STUDIO CARD ----
        self.studio_card = QFrame()
        self.studio_card.setObjectName("studioCard")
        self.studio_card.setMinimumWidth(430)
        self.studio_card.setMaximumWidth(740)
        studio_lay = QVBoxLayout(self.studio_card)
        studio_lay.setContentsMargins(12, 10, 12, 10)
        studio_lay.setSpacing(8)

        # Top Mode Toggle: Simple vs Advanced
        self.seg_ui_mode = SegmentedBar(
            [("✦ 简易模式", "simple"), ("⚙ 高级模式", "advanced")]
        )
        self.seg_ui_mode.selectionChanged.connect(lambda m: self.set_ui_mode(str(m)))
        studio_lay.addWidget(self.seg_ui_mode)

        self.studio_scroll = QScrollArea()
        self.studio_scroll.setWidgetResizable(True)
        self.studio_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.studio_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.studio_body = QWidget()
        self.studio_body.setObjectName("queueChipsHost")
        body_lay = QVBoxLayout(self.studio_body)
        body_lay.setContentsMargins(0, 2, 4, 2)
        body_lay.setSpacing(8)
        self.studio_scroll.setWidget(self.studio_body)
        studio_lay.addWidget(self.studio_scroll, 1)

        # Hidden range spinboxes kept synced with timeline
        self.spin_start = NoWheelSpinBox()
        self.spin_start.valueChanged.connect(self._on_range_spin_changed)
        self.spin_end = NoWheelSpinBox()
        self.spin_end.valueChanged.connect(self._on_range_spin_changed)

        # ======== SIMPLE CONTAINER ========
        self.simple_container = QWidget()
        sim_lay = QVBoxLayout(self.simple_container)
        sim_lay.setContentsMargins(0, 0, 0, 0)
        sim_lay.setSpacing(10)

        sim_lay.addWidget(_section_header("基础设置 (一键出片)"))

        row_sa = QHBoxLayout()
        row_sa.setSpacing(8)
        row_sa.addWidget(_field_lbl("声音开关"))
        self.seg_simple_audio = SegmentedBar([("🔊 保留原声", "keep"), ("🔇 静音", "mute")])
        self.seg_simple_audio.selectionChanged.connect(self._on_simple_audio_changed)
        row_sa.addWidget(self.seg_simple_audio, 1)
        sim_lay.addLayout(row_sa)

        row_sf = QHBoxLayout()
        row_sf.setSpacing(8)
        row_sf.addWidget(_field_lbl("帧率方案"))
        self.seg_simple_fps = SegmentedBar(
            [("原帧率", "keep_original"), ("重排至 30 帧", "raw_30"), ("极速光流补帧", "rife_fast")]
        )
        self.seg_simple_fps.selectionChanged.connect(self._on_simple_fps_changed)
        row_sf.addWidget(self.seg_simple_fps, 1)
        sim_lay.addLayout(row_sf)

        sim_sum_row = QHBoxLayout()
        sim_sum_row.setSpacing(6)
        self.lbl_simple_summary = QLabel("请先导入视频")
        self.lbl_simple_summary.setWordWrap(True)
        self.lbl_simple_summary.setStyleSheet(
            f"background-color: {PALETTE['mint_soft']}; border: 1px solid {PALETTE['sky']}; border-radius: 8px; padding: 7px 10px; color: {PALETTE['ink_primary']};"
        )
        sim_sum_row.addWidget(self.lbl_simple_summary, 1)
        self.btn_fit_3s_simple = QPushButton("✂ 控时3秒")
        self.btn_fit_3s_simple.setObjectName("keyStarBtn")
        self.btn_fit_3s_simple.clicked.connect(self._fit_duration_under_3s)
        sim_sum_row.addWidget(self.btn_fit_3s_simple)
        sim_lay.addLayout(sim_sum_row)
        sim_lay.addWidget(_divider())
        body_lay.addWidget(self.simple_container)

        # ======== ADVANCED CONTAINER ========
        self.advanced_container = QWidget()
        adv_lay = QVBoxLayout(self.advanced_container)
        adv_lay.setContentsMargins(0, 0, 0, 0)
        adv_lay.setSpacing(8)

        # --- Section A: Composable Framing & Polaroid (原画幅 / 比例裁切 + 拍立得相纸) ---
        adv_lay.addWidget(_section_header("画面构图与拍立得"))

        self.seg_framing_mode = SegmentedBar(
            [("原画幅 (不裁切)", "none"), ("✂ 比例裁切", "crop")]
        )
        self.seg_framing_mode.selectionChanged.connect(lambda m: self._on_framing_mode_changed(str(m)))
        adv_lay.addWidget(self.seg_framing_mode)

        # Compatibility crop toggle button (synced with seg_framing_mode / preview.is_crop_editing)
        self.btn_crop_toggle = QPushButton("👁 隐藏框线预览")
        self.btn_crop_toggle.setObjectName("cropToggleBtn")
        self.btn_crop_toggle.setCheckable(True)
        self.btn_crop_toggle.setChecked(False)
        self.btn_crop_toggle.toggled.connect(self._on_crop_toggle_changed)

        # Streamlined Crop Sub-Panel (visible when seg_framing_mode == "crop")
        self.crop_subpanel = QWidget()
        cs_lay = QVBoxLayout(self.crop_subpanel)
        cs_lay.setContentsMargins(0, 2, 0, 0)
        cs_lay.setSpacing(6)

        row_ratio_ori = QHBoxLayout()
        row_ratio_ori.setSpacing(5)
        self.aspect_btn_group = QButtonGroup(self)
        for label, key, _r in CANONICAL_RATIOS:
            ab = ChipButton(label)
            ab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.aspect_btn_group.addButton(ab)
            ab.clicked.connect(lambda _=False, k=key: self._on_aspect_button_clicked(k))
            row_ratio_ori.addWidget(ab, 1)
            self._aspect_buttons[key] = ab
        self._aspect_buttons["orig"].setChecked(True)
        cs_lay.addLayout(row_ratio_ori)

        row_ori_fit = QHBoxLayout()
        row_ori_fit.setSpacing(6)
        self.seg_orientation = SegmentedBar([("▯ 竖屏", "portrait"), ("▭ 横屏", "landscape")])
        self.seg_orientation.selectionChanged.connect(lambda o: self._on_orientation_changed(str(o)))
        self.seg_fit_mode = SegmentedBar([("内裁填满", "inside"), ("外扩留边", "pad")])
        self.seg_fit_mode.selectionChanged.connect(lambda m: self._reapply_current_aspect(str(m)))
        self.btn_pad_margin = QPushButton("+10% 留边")
        self.btn_pad_margin.clicked.connect(self._expand_crop_margin)
        row_ori_fit.addWidget(self.seg_orientation, 1)
        row_ori_fit.addWidget(self.seg_fit_mode, 1)
        row_ori_fit.addWidget(self.btn_pad_margin)
        cs_lay.addLayout(row_ori_fit)

        row_manual = QHBoxLayout()
        row_manual.setSpacing(5)
        row_manual.addWidget(_field_lbl("自定义比"))
        self.edit_ratio_w = QLineEdit()
        self.edit_ratio_w.setPlaceholderText("宽")
        self.edit_ratio_w.setValidator(QDoubleValidator(0.01, 9999.0, 2))
        self.edit_ratio_w.returnPressed.connect(self._apply_manual_ratio)
        self.edit_ratio_h = QLineEdit()
        self.edit_ratio_h.setPlaceholderText("高")
        self.edit_ratio_h.setValidator(QDoubleValidator(0.01, 9999.0, 2))
        self.edit_ratio_h.returnPressed.connect(self._apply_manual_ratio)
        lbl_colon = QLabel(":")
        lbl_colon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_colon.setFixedWidth(10)
        self.btn_apply_ratio = QPushButton("应用")
        self.btn_apply_ratio.clicked.connect(self._apply_manual_ratio)
        row_manual.addWidget(self.edit_ratio_w, 1)
        row_manual.addWidget(lbl_colon)
        row_manual.addWidget(self.edit_ratio_h, 1)
        row_manual.addWidget(self.btn_apply_ratio)
        row_manual.addWidget(self.btn_crop_toggle)
        cs_lay.addLayout(row_manual)

        self.align_buttons: dict[str, QPushButton] = {}
        row_align_top = QHBoxLayout()
        row_align_top.setSpacing(4)
        for lbl, mode in (
            ("✛ 全居中", "center"),
            ("↔ 左右居中", "center_h"),
            ("↕ 上下居中", "center_v"),
        ):
            btn = QPushButton(lbl)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _=False, m=mode: self._on_align_clicked(m))
            row_align_top.addWidget(btn, 1)
            self.align_buttons[mode] = btn
        cs_lay.addLayout(row_align_top)

        row_align_bot = QHBoxLayout()
        row_align_bot.setSpacing(4)
        for lbl, mode in (
            ("⇡ 靠上", "top"),
            ("⇣ 靠下", "bottom"),
            ("⇤ 靠左", "left"),
            ("⇥ 靠右", "right"),
        ):
            btn = QPushButton(lbl)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _=False, m=mode: self._on_align_clicked(m))
            row_align_bot.addWidget(btn, 1)
            self.align_buttons[mode] = btn
        cs_lay.addLayout(row_align_bot)
        adv_lay.addWidget(self.crop_subpanel)

        # Polaroid Sub-Panel (composable: works standalone or on top of cropped frame)
        self.polaroid_subpanel = QWidget()
        pol_vbox = QVBoxLayout(self.polaroid_subpanel)
        pol_vbox.setContentsMargins(0, 2, 0, 0)
        pol_vbox.setSpacing(4)
        pol_hdr = _field_lbl("拍立得相纸 (可先比例裁切再套框)")
        pol_vbox.addWidget(pol_hdr)
        pol_grid = QGridLayout()
        pol_grid.setContentsMargins(0, 0, 0, 0)
        pol_grid.setSpacing(5)
        self.polaroid_btn_group = QButtonGroup(self)

        pb_none = ChipButton("无相框")
        pb_none.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        pb_none.setChecked(True)
        pb_none.clicked.connect(lambda _=False: self._apply_polaroid_preset(None))
        self.polaroid_btn_group.addButton(pb_none)
        pol_grid.addWidget(pb_none, 0, 0)
        self._polaroid_buttons["__none__"] = pb_none

        pol_items: list[tuple[str | None, str, str]] = [
            (pkey, f"{spec.label.replace('Instax ', '').replace('Polaroid ', '')} · {spec.ratio_label}", f"{spec.label} ({int(spec.sheet_w_mm)}×{int(spec.sheet_h_mm)}mm，画幅比 {spec.ratio_label})")
            for pkey, spec in POLAROID_PRESETS.items()
        ]
        pol_items.append(("custom_crop", "保持裁切比例套框", "保留当前比例裁切结果并直接套入拍立得相纸边框"))

        for idx, (pkey, plabel, ptip) in enumerate(pol_items, start=1):
            pb = ChipButton(plabel)
            pb.setToolTip(ptip)
            pb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            pb.clicked.connect(lambda _=False, k=pkey: self._apply_polaroid_preset(k))
            self.polaroid_btn_group.addButton(pb)
            pol_grid.addWidget(pb, idx // 3, idx % 3)
            if pkey is not None:
                self._polaroid_buttons[pkey] = pb
        pol_vbox.addLayout(pol_grid)
        adv_lay.addWidget(self.polaroid_subpanel)

        # Contextual Background Style Panel (only shown for Polaroid or out-of-bounds pad)
        self.bg_style_panel = QWidget()
        bg_lay = QVBoxLayout(self.bg_style_panel)
        bg_lay.setContentsMargins(0, 2, 0, 0)
        bg_lay.setSpacing(5)
        row_bg = QHBoxLayout()
        row_bg.setSpacing(5)
        row_bg.addWidget(_field_lbl("外扩背景"))
        self.bg_btn_group = QButtonGroup(self)
        self.btn_bg_blur = ChipButton("高斯模糊")
        self.btn_bg_white = ChipButton("纯白")
        self.btn_bg_black = ChipButton("纯黑")
        self.btn_bg_pick = ChipButton("🎨 取色")
        self.btn_bg_blur.setChecked(True)
        for b in (self.btn_bg_blur, self.btn_bg_white, self.btn_bg_black, self.btn_bg_pick):
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.bg_btn_group.addButton(b)
            row_bg.addWidget(b, 1)
        self.btn_bg_blur.clicked.connect(self._on_bg_blur_selected)
        self.btn_bg_white.clicked.connect(lambda: self._set_bg_color("#FFFFFF", self.btn_bg_white))
        self.btn_bg_black.clicked.connect(lambda: self._set_bg_color("#000000", self.btn_bg_black))
        self.btn_bg_pick.clicked.connect(self._pick_bg_color)
        bg_lay.addLayout(row_bg)

        self.blur_row_widget = QWidget()
        br_lay = QHBoxLayout(self.blur_row_widget)
        br_lay.setContentsMargins(0, 0, 0, 0)
        br_lay.setSpacing(8)
        br_lay.addWidget(_field_lbl("模糊强度"))
        self.slider_blur = NoWheelSlider(Qt.Orientation.Horizontal)
        self.slider_blur.setRange(8, 80)
        self.slider_blur.setValue(35)
        self.slider_blur.valueChanged.connect(self._on_bg_changed)
        br_lay.addWidget(self.slider_blur, 1)
        self.lbl_blur_val = QLabel("35")
        self.lbl_blur_val.setFixedWidth(28)
        br_lay.addWidget(self.lbl_blur_val)
        bg_lay.addWidget(self.blur_row_widget)
        adv_lay.addWidget(self.bg_style_panel)

        adv_lay.addWidget(_divider())

        # --- Section B: Audio & FPS / RIFE (Zero Dropdowns) ---
        adv_lay.addWidget(_section_header("声音与帧率补帧 (GPU 光流)"))

        row_audio = QHBoxLayout()
        row_audio.setSpacing(8)
        row_audio.addWidget(_field_lbl("声音控制"))
        self.seg_audio = SegmentedBar(
            [("🔊 原声同步", "speed_sync"), ("🔉 原声淡出", "trim_fade"), ("🔇 静音", "mute")]
        )
        self.seg_audio.selectionChanged.connect(self._on_adv_audio_changed)
        row_audio.addWidget(self.seg_audio, 1)
        adv_lay.addLayout(row_audio)

        self.seg_fps_mode = SegmentedBar(
            [("保持原帧率", "keep_original"), ("原始帧重排", "raw_retime"), ("AI 光流补帧", "rife_interpolate")]
        )
        self.seg_fps_mode.selectionChanged.connect(lambda _k: self._on_fps_mode_changed())
        adv_lay.addWidget(self.seg_fps_mode)

        self.rife_panel = QWidget()
        rife_lay = QVBoxLayout(self.rife_panel)
        rife_lay.setContentsMargins(0, 2, 0, 0)
        rife_lay.setSpacing(6)

        row_rm = QHBoxLayout()
        row_rm.setSpacing(5)
        row_rm.addWidget(_field_lbl("补帧方式"))
        self.rife_mode_group = QButtonGroup(self)
        for label, rmode, rmult in (
            ("目标帧率", "target_fps", 1),
            ("2x", "multiplier", 2),
            ("3x", "multiplier", 3),
            ("4x", "multiplier", 4),
        ):
            rb = ChipButton(label)
            rb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.rife_mode_group.addButton(rb)
            rb.clicked.connect(lambda _=False, m=rmode, k=rmult: self._on_rife_mode_clicked(m, k))
            row_rm.addWidget(rb, 1)
            self._rife_mode_buttons[(rmode, rmult)] = rb
        self._rife_mode_buttons[("target_fps", 1)].setChecked(True)
        self.btn_dl_rife = QPushButton("⬇ 下载 RIFE")
        self.btn_dl_rife.setVisible(find_rife() is None)
        self.btn_dl_rife.clicked.connect(self._on_download_rife)
        row_rm.addWidget(self.btn_dl_rife)
        rife_lay.addLayout(row_rm)

        row_rmod = QHBoxLayout()
        row_rmod.setSpacing(5)
        row_rmod.addWidget(_field_lbl("AI 模型"))
        self.rife_model_group = QButtonGroup(self)
        models = self._rife_models if self._rife_models else ["ffmpeg_minterpolate"]
        for m in models:
            lbl = "FFmpeg 光流" if m == "ffmpeg_minterpolate" else (f"{m} 高画质" if "4.25" in m else (f"{m} 极速" if "4.6" in m else m))
            mb = ChipButton(lbl)
            mb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.rife_model_group.addButton(mb)
            mb.clicked.connect(lambda _=False, mv=m: self._on_rife_model_clicked(mv))
            row_rmod.addWidget(mb, 1)
            self._rife_model_buttons[m] = mb
        if self._selected_rife_model in self._rife_model_buttons:
            self._rife_model_buttons[self._selected_rife_model].setChecked(True)
        rife_lay.addLayout(row_rmod)

        row_rgpu = QHBoxLayout()
        row_rgpu.setSpacing(5)
        row_rgpu.addWidget(_field_lbl("硬件加速"))
        self.rife_gpu_group = QButtonGroup(self)
        def _short_gpu(n: str) -> str:
            s = n.replace("NVIDIA ", "").replace("GeForce ", "").replace("AMD ", "").replace("Radeon ", "").strip()
            return s if len(s) <= 22 else s[:21] + "…"

        gpu_items: list[tuple[int, str, str]] = (
            [(gid, f"⚡ {_short_gpu(name)}", f"GPU {gid}: {name}") for gid, name in self._vulkan_gpus]
            if self._vulkan_gpus
            else [(0, "⚡ Vulkan GPU", "Vulkan GPU")]
        )
        gpu_items.append((-1, "CPU", "CPU 软件运算"))
        for gid, glabel, gtip in gpu_items:
            gb = ChipButton(glabel)
            gb.setToolTip(gtip)
            gb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.rife_gpu_group.addButton(gb)
            gb.clicked.connect(lambda _=False, g=gid: self._on_rife_gpu_clicked(g))
            row_rgpu.addWidget(gb, 1 if gid >= 0 else 0)
            self._rife_gpu_buttons[gid] = gb
        if self._selected_rife_gpu in self._rife_gpu_buttons:
            self._rife_gpu_buttons[self._selected_rife_gpu].setChecked(True)
        rife_lay.addLayout(row_rgpu)

        self.rife_policy_row = QWidget()
        rp_lay = QHBoxLayout(self.rife_policy_row)
        rp_lay.setContentsMargins(0, 0, 0, 0)
        rp_lay.setSpacing(6)
        rp_lay.addWidget(_field_lbl("倍率播放"))
        self.seg_rife_policy = SegmentedBar(
            [("保持原时长(倍增帧率)", "keep_duration"), ("自定义帧率(慢放)", "custom_fps")]
        )
        self.seg_rife_policy.selectionChanged.connect(lambda _k: self._on_fps_mode_changed())
        rp_lay.addWidget(self.seg_rife_policy, 1)
        rife_lay.addWidget(self.rife_policy_row)
        adv_lay.addWidget(self.rife_panel)

        self.custom_fps_panel = QWidget()
        cf_lay = QHBoxLayout(self.custom_fps_panel)
        cf_lay.setContentsMargins(0, 2, 0, 0)
        cf_lay.setSpacing(5)
        cf_lay.addWidget(_field_lbl("目标帧率"))
        self.spin_custom_fps = NoWheelDoubleSpinBox()
        self.spin_custom_fps.setRange(1.0, 240.0)
        self.spin_custom_fps.setDecimals(0)
        self.spin_custom_fps.setValue(30.0)
        self.spin_custom_fps.setSuffix(" 帧/秒")
        self.spin_custom_fps.setFixedWidth(104)
        self.spin_custom_fps.valueChanged.connect(self._on_custom_fps_changed)
        cf_lay.addWidget(self.spin_custom_fps)
        for preset_fps in FPS_PRESET_VALUES:
            btn = QPushButton(f"{preset_fps}")
            btn.setObjectName("fpsChipBtn")
            btn.setCheckable(True)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _=False, v=preset_fps: self.spin_custom_fps.setValue(float(v)))
            cf_lay.addWidget(btn, 1)
            self._fps_chip_buttons.append((preset_fps, btn))
        adv_lay.addWidget(self.custom_fps_panel)

        sum_row = QHBoxLayout()
        sum_row.setSpacing(6)
        self.lbl_summary = QLabel("请先导入视频")
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setStyleSheet(
            f"background-color: {PALETTE['mint_soft']}; border: 1px solid {PALETTE['sky']}; border-radius: 8px; padding: 7px 10px; color: {PALETTE['ink_primary']};"
        )
        sum_row.addWidget(self.lbl_summary, 1)
        self.btn_fit_3s = QPushButton("✂ 控时3秒")
        self.btn_fit_3s.setObjectName("keyStarBtn")
        self.btn_fit_3s.setToolTip("不改变帧率，自动在主要图片附近裁切 3 秒片段")
        self.btn_fit_3s.clicked.connect(self._fit_duration_under_3s)
        sum_row.addWidget(self.btn_fit_3s)
        adv_lay.addLayout(sum_row)

        adv_lay.addWidget(_divider())

        # --- Section C: Key Photo (Hide external row when video frame selected) ---
        adv_lay.addWidget(_section_header("实况封面 (主要图片)"))
        self.seg_key_source = SegmentedBar(
            [("使用视频选定帧", "video_frame"), ("使用外部自定义图片", "external_image")]
        )
        self.seg_key_source.selectionChanged.connect(lambda _k: self._on_key_source_changed())
        adv_lay.addWidget(self.seg_key_source)

        c3_body = QHBoxLayout()
        c3_body.setSpacing(8)
        key_left = QVBoxLayout()
        key_left.setSpacing(6)

        self.video_key_row_widget = QWidget()
        row_kf = QHBoxLayout(self.video_key_row_widget)
        row_kf.setContentsMargins(0, 0, 0, 0)
        row_kf.setSpacing(5)
        row_kf.addWidget(_field_lbl("封面帧号"))
        self.spin_key_frame = NoWheelSpinBox()
        self.spin_key_frame.setFixedWidth(74)
        self.spin_key_frame.valueChanged.connect(self._on_key_frame_changed)
        row_kf.addWidget(self.spin_key_frame)
        self.btn_take_cur_key = QPushButton("取当前帧")
        self.btn_take_cur_key.clicked.connect(self._set_current_as_key)
        row_kf.addWidget(self.btn_take_cur_key)
        self.btn_jump_key = QPushButton("跳至封面")
        self.btn_jump_key.clicked.connect(lambda: self._seek_frame(self.spin_key_frame.value()))
        row_kf.addWidget(self.btn_jump_key)
        row_kf.addStretch(1)
        key_left.addWidget(self.video_key_row_widget)

        self.ext_key_container = QWidget()
        ext_lay = QVBoxLayout(self.ext_key_container)
        ext_lay.setContentsMargins(0, 0, 0, 0)
        ext_lay.setSpacing(6)

        row_ext = QHBoxLayout()
        row_ext.setSpacing(5)
        row_ext.addWidget(_field_lbl("外部图片"))
        self.btn_choose_ext = QPushButton("选图片...")
        self.btn_choose_ext.clicked.connect(self._on_choose_external_key)
        row_ext.addWidget(self.btn_choose_ext)
        self.lbl_ext_name = ElidedLabel("未选择外部图片", Qt.TextElideMode.ElideMiddle)
        self.lbl_ext_name.setStyleSheet(f"color: {PALETTE['ink_muted']};")
        row_ext.addWidget(self.lbl_ext_name, 1)
        ext_lay.addLayout(row_ext)

        self.ext_aspect_panel = QWidget()
        ea_lay = QHBoxLayout(self.ext_aspect_panel)
        ea_lay.setContentsMargins(0, 0, 0, 0)
        ea_lay.setSpacing(4)
        ea_lay.addWidget(_field_lbl("比例不同"))
        self.ext_aspect_group = QButtonGroup(self)
        self.btn_ext_crop = ChipButton("居中裁切")
        self.btn_ext_pad = ChipButton("留边全图")
        self.btn_ext_match = ChipButton("随封面比例")
        self.btn_ext_crop.setChecked(True)
        for b, mode_val in (
            (self.btn_ext_crop, "crop_fill"),
            (self.btn_ext_pad, "pad_fit"),
            (self.btn_ext_match, "match_cover_aspect"),
        ):
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.ext_aspect_group.addButton(b)
            b.clicked.connect(lambda _=False, m=mode_val: self._on_ext_aspect_mode_clicked(m))
            ea_lay.addWidget(b, 1)
        ext_lay.addWidget(self.ext_aspect_panel)
        key_left.addWidget(self.ext_key_container)

        self.lbl_key_thumb = QLabel("封面")
        self.lbl_key_thumb.setFixedSize(88, 108)
        self.lbl_key_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_key_thumb.setStyleSheet(
            f"background-color: {PALETTE['surface_subtle']}; border: 1px solid {PALETTE['sky']}; border-radius: 8px;"
        )
        c3_body.addLayout(key_left, 1)
        c3_body.addWidget(self.lbl_key_thumb)
        adv_lay.addLayout(c3_body)

        adv_lay.addWidget(_divider())

        # --- Section D: Export Format, Long Edge (+ Live Scaled Spec), Bitrate, Blank EXIF ---
        adv_lay.addWidget(_section_header("画质规格、码率与相机 EXIF"))

        row_fmt = QHBoxLayout()
        row_fmt.setSpacing(8)
        row_fmt.addWidget(_field_lbl("实况格式"))
        self.seg_img_fmt = SegmentedBar(
            [("HEIC + MOV (原生推荐)", "heic"), ("JPG + MOV (兼容)", "jpg")]
        )
        self.seg_img_fmt.selectionChanged.connect(lambda _k: self._on_export_param_changed())
        row_fmt.addWidget(self.seg_img_fmt, 1)
        adv_lay.addLayout(row_fmt)

        row_edge1 = QHBoxLayout()
        row_edge1.setSpacing(4)
        row_edge1.addWidget(_field_lbl("视频长边"))
        self.long_edge_group = QButtonGroup(self)
        for val in LONG_EDGE_PRESETS:
            txt = "原尺寸" if val == 0 else str(val)
            eb = ChipButton(txt)
            eb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.long_edge_group.addButton(eb)
            eb.clicked.connect(lambda _=False, v=val: self._on_long_edge_preset_clicked(v))
            row_edge1.addWidget(eb, 1)
            self._long_edge_buttons[val] = eb
        self._long_edge_buttons[0].setChecked(True)
        adv_lay.addLayout(row_edge1)

        row_edge2 = QHBoxLayout()
        row_edge2.setSpacing(6)
        row_edge2.addWidget(_field_lbl("自定义px"))
        self.spin_custom_edge = NoWheelSpinBox()
        self.spin_custom_edge.setRange(0, 8192)
        self.spin_custom_edge.setSingleStep(2)
        self.spin_custom_edge.setSpecialValueText("原尺寸")
        self.spin_custom_edge.setSuffix(" px")
        self.spin_custom_edge.setFixedWidth(108)
        self.spin_custom_edge.valueChanged.connect(self._on_custom_edge_changed)
        row_edge2.addWidget(self.spin_custom_edge)
        self.lbl_scaled_res_spec = ElidedLabel("当前规格: -", Qt.TextElideMode.ElideRight)
        self.lbl_scaled_res_spec.setObjectName("metaCapsule")
        row_edge2.addWidget(self.lbl_scaled_res_spec, 1)
        adv_lay.addLayout(row_edge2)

        row_br = QHBoxLayout()
        row_br.setSpacing(6)
        row_br.addWidget(_field_lbl("输出码率"))
        self.slider_bitrate = NoWheelSlider(Qt.Orientation.Horizontal)
        self.slider_bitrate.setRange(4, 80)
        self.slider_bitrate.setValue(24)
        self.slider_bitrate.valueChanged.connect(self._on_bitrate_changed)
        row_br.addWidget(self.slider_bitrate, 1)
        self.lbl_bitrate_val = QLabel("24 Mbps")
        self.lbl_bitrate_val.setFixedWidth(64)
        self.lbl_bitrate_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row_br.addWidget(self.lbl_bitrate_val)
        adv_lay.addLayout(row_br)

        self.lbl_est_size = ElidedLabel("预计输出体积: -", Qt.TextElideMode.ElideRight)
        self.lbl_est_size.setObjectName("metaCapsule")
        adv_lay.addWidget(self.lbl_est_size)

        row_exif = QHBoxLayout()
        row_exif.setSpacing(6)
        row_exif.addWidget(_field_lbl("相机 EXIF"))
        self.lbl_exif_summary = ElidedLabel("空白 (不写入相机参数)", Qt.TextElideMode.ElideRight)
        self.lbl_exif_summary.setObjectName("metaCapsule")
        row_exif.addWidget(self.lbl_exif_summary, 1)
        adv_lay.addLayout(row_exif)

        row_exif_btns = QHBoxLayout()
        row_exif_btns.setSpacing(6)
        self.btn_edit_exif = QPushButton("📷 修改 / 导入 EXIF...")
        self.btn_edit_exif.clicked.connect(self._on_open_exif_dialog)
        self.btn_auto_exif = QPushButton("📂 选目录匹配同名RAW...")
        self.btn_auto_exif.setToolTip("手动选择存放 RAW/照片的文件夹，自动按文件名匹配导入 EXIF")
        self.btn_auto_exif.clicked.connect(self._on_pick_folder_auto_exif)
        row_exif_btns.addWidget(self.btn_edit_exif, 1)
        row_exif_btns.addWidget(self.btn_auto_exif, 1)
        adv_lay.addLayout(row_exif_btns)
        adv_lay.addWidget(_divider())
        body_lay.addWidget(self.advanced_container)

        # ======== SHARED SECTION: OUTPUT DIRECTORY & NAMING ========
        self.shared_export_container = QWidget()
        sh_lay = QVBoxLayout(self.shared_export_container)
        sh_lay.setContentsMargins(0, 0, 0, 0)
        sh_lay.setSpacing(7)

        sh_lay.addWidget(_section_header("输出目录与命名"))

        self.seg_export_mode = SegmentedBar(
            [("普通文件夹", "folder"), ("U盘相机 (DCIM)", "usb_dcim")]
        )
        self.seg_export_mode.selectionChanged.connect(lambda _k: self._on_export_mode_changed())
        sh_lay.addWidget(self.seg_export_mode)

        self.usb_panel = QWidget()
        usb_lay = QVBoxLayout(self.usb_panel)
        usb_lay.setContentsMargins(0, 2, 0, 0)
        usb_lay.setSpacing(6)
        row_drv = QHBoxLayout()
        row_drv.setSpacing(6)
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

        self.lbl_usb_dest_preview = ElidedLabel("", Qt.TextElideMode.ElideMiddle)
        self.lbl_usb_dest_preview.setStyleSheet(f"color: {PALETTE['ink_secondary']};")
        usb_lay.addWidget(self.lbl_usb_dest_preview)
        sh_lay.addWidget(self.usb_panel)

        self.folder_panel = QWidget()
        fol_vbox = QVBoxLayout(self.folder_panel)
        fol_vbox.setContentsMargins(0, 2, 0, 0)
        fol_vbox.setSpacing(6)

        fol_lay = QHBoxLayout()
        fol_lay.setSpacing(6)
        self.edit_folder = QLineEdit(str(Path.home() / "Desktop" / "LivePhoto_Output"))
        self.edit_folder.textChanged.connect(self._update_export_preview)
        self.btn_browse_folder = QPushButton("浏览...")
        self.btn_browse_folder.clicked.connect(self._browse_output_folder)
        self.chk_livp = StyledCheckBox(".livp")
        fol_lay.addWidget(self.edit_folder, 1)
        fol_lay.addWidget(self.btn_browse_folder)
        fol_lay.addWidget(self.chk_livp)
        fol_vbox.addLayout(fol_lay)

        self.lbl_folder_dest_preview = ElidedLabel("", Qt.TextElideMode.ElideMiddle)
        self.lbl_folder_dest_preview.setStyleSheet(f"color: {PALETTE['ink_secondary']};")
        fol_vbox.addWidget(self.lbl_folder_dest_preview)
        sh_lay.addWidget(self.folder_panel)

        self.folder_naming_widget = QWidget()
        fn_lay = QVBoxLayout(self.folder_naming_widget)
        fn_lay.setContentsMargins(0, 2, 0, 0)
        fn_lay.setSpacing(5)
        self.seg_folder_naming = SegmentedBar(
            [("源名+后缀", "suffix"), ("原文件名", "original"), ("前缀+序号", "prefix_seq"), ("自定义", "custom")]
        )
        self.seg_folder_naming.selectionChanged.connect(lambda _k: self._on_folder_naming_changed())
        fn_lay.addWidget(self.seg_folder_naming)

        fn_sub = QHBoxLayout()
        fn_sub.setSpacing(5)
        self.lbl_fn_param = _field_lbl("文件后缀")
        self.edit_folder_suffix = QLineEdit("_2live")
        self.edit_folder_suffix.setFixedWidth(96)
        self.edit_folder_suffix.textChanged.connect(self._update_export_preview)
        self.edit_folder_prefix = QLineEdit("LIVE_")
        self.edit_folder_prefix.setFixedWidth(100)
        self.edit_folder_prefix.textChanged.connect(self._update_export_preview)
        self.spin_folder_seq = FourDigitSpinBox()
        self.spin_folder_seq.setRange(1, 9999)
        self.spin_folder_seq.setValue(1)
        self.spin_folder_seq.setFixedWidth(74)
        self.spin_folder_seq.valueChanged.connect(self._update_export_preview)
        self.chk_avoid_overwrite = StyledCheckBox("防重名自动编号")
        self.chk_avoid_overwrite.setChecked(True)
        fn_sub.addWidget(self.lbl_fn_param)
        fn_sub.addWidget(self.edit_folder_suffix)
        fn_sub.addWidget(self.edit_folder_prefix)
        fn_sub.addWidget(self.spin_folder_seq)
        fn_sub.addWidget(self.chk_avoid_overwrite, 1)
        fn_lay.addLayout(fn_sub)
        sh_lay.addWidget(self.folder_naming_widget)

        self.chk_hw_encode = StyledCheckBox(
            "NVIDIA NVENC GPU 硬件编码" if self._nvenc_available else "CPU x264 编码 (未检测到 NVENC)"
        )
        self.chk_hw_encode.setChecked(self._nvenc_available)
        self.chk_hw_encode.setEnabled(self._nvenc_available)
        sh_lay.addWidget(self.chk_hw_encode)

        body_lay.addWidget(self.shared_export_container)
        body_lay.addStretch(1)

        # ---- PINNED EXPORT FOOTER INSIDE STUDIO CARD ----
        studio_lay.addWidget(_divider())
        row_act = QHBoxLayout()
        row_act.setSpacing(6)
        self.btn_convert = QPushButton("✦ 开始生成实况照片")
        self.btn_convert.setObjectName("primaryBtn")
        self.btn_convert.setEnabled(False)
        self.btn_convert.clicked.connect(self._on_start_convert)
        self.btn_convert_all = QPushButton("✦ 批量生成")
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
        studio_lay.addLayout(row_act)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        studio_lay.addWidget(self.progress_bar)

        self.lbl_status = ElidedLabel(
            "就绪 · U盘插 iPhone 后打开「照片」App → 右下角「导入」即可合成实况",
            Qt.TextElideMode.ElideRight,
        )
        self.lbl_status.setStyleSheet(f"color: {PALETTE['ink_secondary']};")
        studio_lay.addWidget(self.lbl_status)

        self.main_splitter.addWidget(self.studio_card)
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 0)
        self.main_splitter.setSizes([950, 560])

        self._on_framing_mode_changed("none")
        self._on_bg_changed()
        self._on_fps_mode_changed()
        self._on_key_source_changed()
        self._on_folder_naming_changed()
        self._on_export_mode_changed()
        self._on_custom_fps_changed(self.spin_custom_fps.value())
        self._rebuild_queue_bar()

    # ---------- Simple vs Advanced Mode ----------

    def set_ui_mode(self, mode: str) -> None:
        self._ui_mode = "advanced" if mode == "advanced" else "simple"
        is_adv = self._ui_mode == "advanced"
        self.seg_ui_mode.setCurrentKey(self._ui_mode)
        self.simple_container.setVisible(not is_adv)
        self.advanced_container.setVisible(is_adv)
        self.folder_naming_widget.setVisible(is_adv)
        self.chk_livp.setVisible(is_adv)
        self.chk_hw_encode.setVisible(is_adv)

        if not is_adv:
            self._sync_simple_controls_from_state()
        self._update_summary()
        self._update_export_preview()

    def _sync_simple_controls_from_state(self) -> None:
        self._syncing_ui = True
        try:
            audio_key = str(self.seg_audio.currentKey() or "speed_sync")
            self.seg_simple_audio.setCurrentKey("mute" if audio_key == "mute" else "keep")

            fps_key = str(self.seg_fps_mode.currentKey() or "keep_original")
            if fps_key == "rife_interpolate":
                self.seg_simple_fps.setCurrentKey("rife_fast")
            elif fps_key == "raw_retime" and abs(self.spin_custom_fps.value() - 30.0) < 0.5:
                self.seg_simple_fps.setCurrentKey("raw_30")
            else:
                self.seg_simple_fps.setCurrentKey("keep_original")
        finally:
            self._syncing_ui = False

    def _on_simple_audio_changed(self, key: object) -> None:
        if self._syncing_ui:
            return
        self._syncing_ui = True
        try:
            if key == "mute":
                self.seg_audio.setCurrentKey("mute")
            elif self.seg_audio.currentKey() == "mute":
                self.seg_audio.setCurrentKey("speed_sync")
        finally:
            self._syncing_ui = False
        self._update_summary()

    def _on_adv_audio_changed(self, key: object) -> None:
        if not self._syncing_ui:
            self._syncing_ui = True
            self.seg_simple_audio.setCurrentKey("mute" if key == "mute" else "keep")
            self._syncing_ui = False
        self._update_summary()

    def _on_simple_fps_changed(self, key: object) -> None:
        if self._syncing_ui:
            return
        self._syncing_ui = True
        try:
            if key == "keep_original":
                self.seg_fps_mode.setCurrentKey("keep_original")
            elif key == "raw_30":
                self.seg_fps_mode.setCurrentKey("raw_retime")
                self.spin_custom_fps.setValue(30.0)
            elif key == "rife_fast":
                self.seg_fps_mode.setCurrentKey("rife_interpolate")
                self._selected_rife_mode = "target_fps"
                self._selected_rife_mult = 1
                if ("target_fps", 1) in self._rife_mode_buttons:
                    self._rife_mode_buttons[("target_fps", 1)].setChecked(True)
                self.spin_custom_fps.setValue(30.0)
                fast_model = "rife-v4.6" if "rife-v4.6" in self._rife_models else (
                    self._rife_models[0] if self._rife_models else "ffmpeg_minterpolate"
                )
                self._selected_rife_model = fast_model
                if fast_model in self._rife_model_buttons:
                    self._rife_model_buttons[fast_model].setChecked(True)
                self._selected_rife_gpu = self._vulkan_gpus[0][0] if self._vulkan_gpus else 0
                if self._selected_rife_gpu in self._rife_gpu_buttons:
                    self._rife_gpu_buttons[self._selected_rife_gpu].setChecked(True)
        finally:
            self._syncing_ui = False
        self._on_fps_mode_changed()

    # ---------- Compatibility Properties ----------

    @property
    def rb_keep(self) -> QPushButton:
        b = self.seg_fps_mode.buttonForKey("keep_original")
        assert b is not None
        return b

    @property
    def rb_retime(self) -> QPushButton:
        b = self.seg_fps_mode.buttonForKey("raw_retime")
        assert b is not None
        return b

    @property
    def rb_rife(self) -> QPushButton:
        b = self.seg_fps_mode.buttonForKey("rife_interpolate")
        assert b is not None
        return b

    @property
    def rb_key_video(self) -> QPushButton:
        b = self.seg_key_source.buttonForKey("video_frame")
        assert b is not None
        return b

    @property
    def rb_key_ext(self) -> QPushButton:
        b = self.seg_key_source.buttonForKey("external_image")
        assert b is not None
        return b

    @property
    def rb_export_usb(self) -> QPushButton:
        b = self.seg_export_mode.buttonForKey("usb_dcim")
        assert b is not None
        return b

    @property
    def rb_export_folder(self) -> QPushButton:
        b = self.seg_export_mode.buttonForKey("folder")
        assert b is not None
        return b

    # ---------- Queue & Video Loading ----------

    def _auto_load_same_dir_exif(self, video_path: Path) -> ExifMetadata | None:
        try:
            match = find_matching_sidecar_exif_file(video_path, search_dir=None)
            if match is None:
                return None
            meta = read_exif_from_file(match)
            return None if meta.is_empty() else meta
        except Exception:
            return None

    def _default_item_state(self, info: VideoInfo, thumb: QPixmap | None) -> QueueItemState:
        return QueueItemState(
            info=info,
            thumb_pixmap=thumb,
            start_frame=0,
            end_frame=max(0, info.nb_frames - 1),
            crop=CropRect(0, 0, info.width, info.height),
            padding=FramePadding(),
            framing_mode="none",
            polaroid_preset=None,
            aspect_key="orig",
            custom_ratio=None,
            orientation="portrait" if info.height >= info.width else "landscape",
            audio_mode="speed_sync",
            bg_mode="blur" if self.btn_bg_blur.isChecked() else "solid",
            bg_color=self._bg_color_hex,
            bg_blur_sigma=self.slider_blur.value(),
            fps_mode="keep_original",
            rife_mode="target_fps",
            rife_multiplier=1,
            rife_model=self._selected_rife_model,
            rife_gpu_id=self._selected_rife_gpu,
            rife_playback_policy="keep_duration",
            custom_fps=30.0,
            key_source="video_frame",
            key_frame_index=min(max(0, info.nb_frames - 1), max(0, info.nb_frames - 1) // 2),
            external_key_path=None,
            external_key_aspect_mode="crop_fill",
            video_max_edge=self.spin_custom_edge.value(),
            video_bitrate_mbps=float(self.slider_bitrate.value()),
            image_format=str(self.seg_img_fmt.currentKey() or "heic"),
            exif_metadata=self._auto_load_same_dir_exif(info.path),
        )

    def _capture_active_state(self) -> None:
        if self._active_idx < 0 or self._active_idx >= len(self._queue) or self._video_info is None:
            return
        st = self._queue[self._active_idx]
        st.start_frame = self.spin_start.value()
        st.end_frame = self.spin_end.value()
        st.crop = self.preview.get_crop()
        st.padding = self.preview.get_padding()
        st.framing_mode = self._framing_mode
        st.polaroid_preset = self._active_polaroid
        st.aspect_key = self._active_aspect_key
        st.custom_ratio = self._custom_ratio
        st.orientation = self._orientation
        st.audio_mode = str(self.seg_audio.currentKey() or "speed_sync")
        st.bg_mode = "blur" if self.btn_bg_blur.isChecked() else "solid"
        st.bg_color = self._bg_color_hex
        st.bg_blur_sigma = self.slider_blur.value()
        st.fps_mode = str(self.seg_fps_mode.currentKey() or "keep_original")
        st.rife_mode = self._selected_rife_mode
        st.rife_multiplier = self._selected_rife_mult
        st.rife_model = self._selected_rife_model
        st.rife_gpu_id = self._selected_rife_gpu
        st.rife_playback_policy = str(self.seg_rife_policy.currentKey() or "keep_duration")
        st.custom_fps = self.spin_custom_fps.value()
        st.key_source = str(self.seg_key_source.currentKey() or "video_frame")
        st.key_frame_index = self.spin_key_frame.value()
        st.external_key_path = self._external_key_path
        st.external_key_aspect_mode = self._selected_ext_aspect_mode()
        st.video_max_edge = self.spin_custom_edge.value()
        st.video_bitrate_mbps = float(self.slider_bitrate.value())
        st.image_format = str(self.seg_img_fmt.currentKey() or "heic")
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

        if st.thumb_pixmap is None:
            qimg0 = self._frame_provider.get_frame(0)
            if qimg0 is not None:
                st.thumb_pixmap = QPixmap.fromImage(qimg0)

        self._syncing_ui = True
        try:
            self.lbl_full_dir_info.setText(f"源目录: {info.path.parent}   |   文件: {info.path.name}")
            audio_tag = "含音轨" if info.has_audio else "无音轨"
            self.lbl_video_info.setText(
                f"源规格: {info.width}×{info.height} · {info.fps:.0f} 帧/秒 · {info.nb_frames} 帧 "
                f"({format_frames_as_sec_frames(info.nb_frames, info.fps)}) · {audio_tag}"
            )

            max_idx = max(0, info.nb_frames - 1)
            self.spin_start.setRange(0, max_idx)
            self.spin_end.setRange(0, max_idx)
            self.spin_key_frame.setRange(0, max_idx)
            self.spin_start.setValue(st.start_frame)
            self.spin_end.setValue(st.end_frame)
            self.spin_key_frame.setValue(st.key_frame_index)

            self._framing_mode = st.framing_mode if st.framing_mode in ("none", "crop") else "none"
            self.seg_framing_mode.setCurrentKey(self._framing_mode)
            self._active_aspect_key = st.aspect_key
            self._custom_ratio = st.custom_ratio
            self._orientation = st.orientation
            self.seg_orientation.setCurrentKey(st.orientation)
            self._update_ratio_button_labels()
            if st.aspect_key in self._aspect_buttons:
                self._aspect_buttons[st.aspect_key].setChecked(True)
            else:
                self.aspect_btn_group.setExclusive(False)
                for ab in self._aspect_buttons.values():
                    ab.setChecked(False)
                self.aspect_btn_group.setExclusive(True)
            self._active_polaroid = st.polaroid_preset
            if st.polaroid_preset and st.polaroid_preset in self._polaroid_buttons:
                self._polaroid_buttons[st.polaroid_preset].setChecked(True)
            elif "__none__" in self._polaroid_buttons:
                self._polaroid_buttons["__none__"].setChecked(True)

            self.seg_audio.setCurrentKey(st.audio_mode)
            self.seg_simple_audio.setCurrentKey("mute" if st.audio_mode == "mute" else "keep")

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

            fps_key = "rife_interpolate" if st.fps_mode == "ffmpeg_minterpolate" else st.fps_mode
            self.seg_fps_mode.setCurrentKey(fps_key)
            self._selected_rife_mode = st.rife_mode
            self._selected_rife_mult = st.rife_multiplier
            if (st.rife_mode, st.rife_multiplier) in self._rife_mode_buttons:
                self._rife_mode_buttons[(st.rife_mode, st.rife_multiplier)].setChecked(True)
            self._selected_rife_model = st.rife_model
            if st.rife_model in self._rife_model_buttons:
                self._rife_model_buttons[st.rife_model].setChecked(True)
            self._selected_rife_gpu = st.rife_gpu_id
            if st.rife_gpu_id in self._rife_gpu_buttons:
                self._rife_gpu_buttons[st.rife_gpu_id].setChecked(True)
            self.seg_rife_policy.setCurrentKey(st.rife_playback_policy)
            self.spin_custom_fps.setValue(st.custom_fps)

            self._external_key_path = st.external_key_path
            self.lbl_ext_name.setText(st.external_key_path.name if st.external_key_path else "未选择外部图片")
            self.seg_key_source.setCurrentKey(st.key_source)

            self.seg_img_fmt.setCurrentKey(st.image_format)
            self.spin_custom_edge.setValue(st.video_max_edge)
            for val, eb in self._long_edge_buttons.items():
                eb.setChecked(val == st.video_max_edge)
            self.slider_bitrate.setValue(int(round(st.video_bitrate_mbps)))

            self._exif_metadata = st.exif_metadata
            self.lbl_exif_summary.setText(
                st.exif_metadata.summary_text() if st.exif_metadata else "空白 (不写入相机参数)"
            )

            crop_active = st.framing_mode == "crop"
            self.btn_crop_toggle.setChecked(False)
            self.preview.set_crop_editing(crop_active)
            self.preview.reset_view()
            self.preview.set_padding(st.padding)
            self.preview.set_crop(st.crop)
            self._sync_framing_visibility()
            self._refresh_canvas_res_label()
            self.btn_convert.setEnabled(True)
        finally:
            self._syncing_ui = False

        self._sync_simple_controls_from_state()
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
        self.btn_convert_all.setVisible(count > 1)
        self.btn_queue_sync.setVisible(count > 1)
        self.btn_queue_clear.setVisible(count >= 1)

        if count == 0:
            hint = QLabel("队列为空 · 请在左下角点击「导入视频」或直接拖入多个视频")
            hint.setStyleSheet(f"color: {PALETTE['ink_muted']};")
            self.queue_chips_lay.addWidget(hint)
            self.queue_chips_lay.addStretch(1)
            return

        for idx, st in enumerate(self._queue):
            dur_txt = format_frames_as_sec_frames(st.info.nb_frames, st.info.fps)
            card = QueueThumbCard(
                idx=idx,
                title=st.info.path.name,
                subtitle=f"{st.info.width}×{st.info.height} · {dur_txt}",
                thumb=st.thumb_pixmap,
                active=(idx == self._active_idx),
                show_remove=(count > 1),
            )
            card.clicked.connect(self._switch_queue_item)
            card.removeRequested.connect(self._remove_queue_item)
            self.queue_chips_lay.addWidget(card)

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
        self.lbl_full_dir_info.setText("源目录: 未加载视频 (支持拖入多个 MP4 / MOV 文件)")
        self.lbl_video_info.setText("源规格: -")
        self.lbl_out_file_info.setText("输出文件: -")
        self.lbl_out_spec_info.setText("输出规格: -")
        self.btn_convert.setEnabled(False)
        self._rebuild_queue_bar()

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
            else:
                st.polaroid_preset = None
                st.padding = FramePadding()
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
            thumb = self._extract_quick_thumb(p)
            self._queue.append(self._default_item_state(info, thumb))
            if added_first == -1:
                added_first = len(self._queue) - 1

        if added_first != -1:
            self._apply_queue_item(added_first)

    def _extract_quick_thumb(self, path: Path) -> QPixmap | None:
        try:
            import cv2

            cap = cv2.VideoCapture(str(path))
            ok, bgr = cap.read()
            cap.release()
            if not ok or bgr is None:
                return None
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            from PySide6.QtGui import QImage

            qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
            return QPixmap.fromImage(qimg)
        except Exception:
            return None

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
            f"当前: 第 {self._current_frame} 帧  |  设定时刻: {cur_txt} / {tot_txt}"
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
        self.seg_key_source.setCurrentKey("video_frame")
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

    # ---------- Composable Framing, Polaroid, Orientation & Alignment ----------

    def _sync_framing_visibility(self) -> None:
        is_crop = self._framing_mode == "crop"
        is_pol = self._active_polaroid is not None
        self.crop_subpanel.setVisible(is_crop)
        self.polaroid_subpanel.setVisible(True)

        is_oob = False
        if self._video_info is not None:
            is_oob = self.preview.get_crop().is_out_of_bounds(self._video_info.width, self._video_info.height)
        self.bg_style_panel.setVisible(is_pol or is_oob)

    def _on_framing_mode_changed(self, mode: str) -> None:
        if self._syncing_ui:
            return
        self._framing_mode = "crop" if mode == "crop" else "none"
        if self._framing_mode == "none":
            self._clear_polaroid_selection()
            self.btn_crop_toggle.setChecked(False)
            self.preview.set_crop_editing(False)
            if self._video_info is not None:
                self.preview.set_crop(CropRect(0, 0, self._video_info.width, self._video_info.height), emit=True)
        else:
            self.btn_crop_toggle.setChecked(False)
            self.preview.set_crop_editing(True)
        self._sync_framing_visibility()
        self._refresh_canvas_res_label()
        self._update_size_estimate()

    def _on_crop_toggle_changed(self, hide_box: bool) -> None:
        if self._framing_mode == "crop":
            self.preview.set_crop_editing(not hide_box)
        self.btn_crop_toggle.setText("✏ 显示裁切框" if hide_box else "👁 隐藏框线预览")

    def _on_preview_crop_editing_changed(self, editing: bool) -> None:
        hide_box = not editing
        self.btn_crop_toggle.blockSignals(True)
        self.btn_crop_toggle.setChecked(hide_box)
        self.btn_crop_toggle.setText("✏ 显示裁切框" if hide_box else "👁 隐藏框线预览")
        self.btn_crop_toggle.blockSignals(False)

    def _on_zoom_changed(self, zoom: float) -> None:
        pct = int(round(zoom * 100))
        self.btn_zoom_reset.setText(f"{pct}% 适应")

    def _refresh_canvas_res_label(self) -> None:
        if self._video_info is None:
            self.lbl_crop_res.setText("画布: -")
            self.lbl_scaled_res_spec.setText("当前规格: -")
            return
        c = self.preview.get_crop()
        pad = self.preview.get_padding()
        tw = c.w + pad.left + pad.right
        th = c.h + pad.top + pad.bottom
        if self._active_polaroid and not pad.is_zero():
            spec = POLAROID_PRESETS.get(self._active_polaroid)
            tag = f" ({spec.label})" if spec is not None else " (拍立得套框)"
        elif c.is_out_of_bounds(self._video_info.width, self._video_info.height) or not pad.is_zero():
            tag = " (外扩)"
        else:
            tag = ""
        self.lbl_crop_res.setText(f"画布: {tw}×{th}{tag}")

        edge = self.spin_custom_edge.value()
        ow, oh = compute_scaled_video_dims(tw, th, edge)
        if edge == 0:
            self.lbl_scaled_res_spec.setText(f"当前规格: {ow}×{oh} px (源尺寸)")
        elif max(tw, th) <= edge:
            self.lbl_scaled_res_spec.setText(f"当前规格: {ow}×{oh} px (不放大)")
        else:
            self.lbl_scaled_res_spec.setText(f"当前规格: {ow}×{oh} px")

    def _on_crop_changed(self, crop: CropRect) -> None:
        if self._video_info is None:
            return
        self._sync_framing_visibility()
        self._refresh_canvas_res_label()
        self._refresh_key_thumbnail()
        self._update_size_estimate()

    def _clear_polaroid_selection(self) -> None:
        self._active_polaroid = None
        if "__none__" in self._polaroid_buttons:
            self._polaroid_buttons["__none__"].setChecked(True)
        else:
            for pb in self._polaroid_buttons.values():
                pb.setChecked(False)
        self.preview.set_padding(FramePadding())

    def _update_ratio_button_labels(self) -> None:
        is_land = self._orientation == "landscape"
        for label, key, _r in CANONICAL_RATIOS:
            if ":" in label and key != "1:1":
                a, b = label.split(":")
                self._aspect_buttons[key].setText(f"{b}:{a}" if is_land else f"{a}:{b}")

    def _ensure_crop_box_visible(self) -> None:
        if self._framing_mode != "crop":
            self._framing_mode = "crop"
            self.seg_framing_mode.setCurrentKey("crop")
        self.btn_crop_toggle.setChecked(False)
        self.preview.set_crop_editing(True)
        self._sync_framing_visibility()

    def _on_orientation_changed(self, ori: str) -> None:
        if self._syncing_ui:
            return
        self._orientation = "landscape" if ori == "landscape" else "portrait"
        self._update_ratio_button_labels()
        if self._video_info is None:
            return
        self._ensure_crop_box_visible()
        fit_mode = str(self.seg_fit_mode.currentKey() or "inside")
        if self._active_aspect_key == "orig":
            src_ratio = self._video_info.width / self._video_info.height
            target_r = max(src_ratio, 1.0 / src_ratio) if self._orientation == "landscape" else min(src_ratio, 1.0 / src_ratio)
            self.preview.apply_aspect_fit(target_r, mode=fit_mode)
        elif self._active_aspect_key == "free":
            self.preview.swap_crop_orientation(fit_mode=fit_mode)
        else:
            self._reapply_current_aspect(fit_mode)

    def _on_aspect_button_clicked(self, key: str) -> None:
        self._active_aspect_key = key
        self._custom_ratio = None
        self._clear_polaroid_selection()
        fit_mode = str(self.seg_fit_mode.currentKey() or "inside")
        self._reapply_current_aspect(fit_mode)

    def _reapply_current_aspect(self, fit_mode: str) -> None:
        if self._video_info is None:
            return
        self._clear_polaroid_selection()
        self._ensure_crop_box_visible()
        ratio: float | None = None
        if self._active_aspect_key == "custom" and self._custom_ratio is not None:
            r = self._custom_ratio
            if self._orientation == "landscape" and r < 1.0:
                ratio = 1.0 / r
            elif self._orientation == "portrait" and r > 1.0:
                ratio = 1.0 / r
            else:
                ratio = r
        else:
            for _lbl, k, r in CANONICAL_RATIOS:
                if k == self._active_aspect_key:
                    if r is None:
                        src_r = self._video_info.width / self._video_info.height
                        ratio = max(src_r, 1.0 / src_r) if self._orientation == "landscape" else min(src_r, 1.0 / src_r)
                    elif r < 0:
                        ratio = None
                    else:
                        ratio = (1.0 / r) if (self._orientation == "landscape" and r < 1.0) else r
                    break
        self.preview.apply_aspect_fit(ratio, mode=fit_mode)
        self._refresh_canvas_res_label()
        self._update_size_estimate()

    def _apply_manual_ratio(self) -> None:
        if self._video_info is None:
            return
        w_txt = self.edit_ratio_w.text().strip()
        h_txt = self.edit_ratio_h.text().strip()
        if not w_txt or not h_txt:
            return
        try:
            w_val = float(w_txt)
            h_val = float(h_txt)
        except ValueError:
            QMessageBox.warning(self, "比例无效", "请在两个输入框中分别输入正数，如 16 和 9。")
            return
        if w_val <= 0 or h_val <= 0:
            return
        ratio = w_val / h_val
        self._active_aspect_key = "custom"
        self._custom_ratio = ratio
        if w_val != h_val:
            self._orientation = "landscape" if w_val > h_val else "portrait"
            self.seg_orientation.setCurrentKey(self._orientation)
            self._update_ratio_button_labels()
        self.aspect_btn_group.setExclusive(False)
        for ab in self._aspect_buttons.values():
            ab.setChecked(False)
        self.aspect_btn_group.setExclusive(True)
        self._clear_polaroid_selection()
        self._ensure_crop_box_visible()
        fit_mode = str(self.seg_fit_mode.currentKey() or "inside")
        self.preview.apply_aspect_fit(ratio, mode=fit_mode)
        self._refresh_canvas_res_label()
        self._update_size_estimate()

    def _on_align_clicked(self, mode: str) -> None:
        if self._video_info is None:
            return
        self._ensure_crop_box_visible()
        self.preview.align_crop(mode)

    def _apply_polaroid_preset(self, preset_key: str | None) -> None:
        if preset_key is None:
            self._clear_polaroid_selection()
            self._sync_framing_visibility()
            self._refresh_canvas_res_label()
            self._update_size_estimate()
            return
        if self._video_info is None:
            return
        self._active_polaroid = preset_key
        if preset_key in self._polaroid_buttons:
            self._polaroid_buttons[preset_key].setChecked(True)

        preserve = self._framing_mode == "crop"
        crop, pad = compute_polaroid_layout(
            preset_key,
            self._video_info.width,
            self._video_info.height,
            current_crop=self.preview.get_crop(),
            preserve_crop_scale=preserve,
        )
        if self.btn_bg_blur.isChecked():
            self._set_bg_color("#FFFFFF", self.btn_bg_white)
        self.preview.set_crop_editing(False)
        self.preview.set_padding(pad)
        self.preview.set_crop(crop, emit=True)
        self._sync_framing_visibility()
        self._refresh_canvas_res_label()
        self._update_size_estimate()

    def _expand_crop_margin(self) -> None:
        if self._video_info is None:
            return
        self._ensure_crop_box_visible()
        c = self.preview.get_crop()
        nw = int(round(c.w * 1.10))
        nh = int(round(c.h * 1.10))
        nx = c.x - (nw - c.w) // 2
        ny = c.y - (nh - c.h) // 2
        self.preview.set_crop(CropRect(nx, ny, nw, nh), emit=True)

    def _reset_crop(self) -> None:
        if self._video_info is None:
            return
        self._framing_mode = "none"
        self.seg_framing_mode.setCurrentKey("none")
        self._active_aspect_key = "orig"
        self._custom_ratio = None
        self._aspect_buttons["orig"].setChecked(True)
        self._clear_polaroid_selection()
        self.preview.set_crop_editing(False)
        self.preview.set_crop(CropRect(0, 0, self._video_info.width, self._video_info.height), emit=True)
        self._sync_framing_visibility()
        self._refresh_canvas_res_label()
        self._update_size_estimate()

    def _on_bg_blur_selected(self) -> None:
        self._on_bg_changed()

    def _on_bg_changed(self) -> None:
        is_blur = self.btn_bg_blur.isChecked()
        self.blur_row_widget.setVisible(is_blur)
        self.lbl_blur_val.setText(str(self.slider_blur.value()))
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

    # ---------- FPS & RIFE Buttons ----------

    def _on_rife_mode_clicked(self, rmode: str, rmult: int) -> None:
        self._selected_rife_mode = rmode
        self._selected_rife_mult = rmult
        self._on_fps_mode_changed()

    def _on_rife_model_clicked(self, model_name: str) -> None:
        self._selected_rife_model = model_name
        self._update_summary()

    def _on_rife_gpu_clicked(self, gpu_id: int) -> None:
        self._selected_rife_gpu = gpu_id

    def _on_fps_mode_changed(self) -> None:
        mode = str(self.seg_fps_mode.currentKey() or "keep_original")
        is_retime = mode == "raw_retime"
        is_rife = mode == "rife_interpolate"
        self.rife_panel.setVisible(is_rife)

        is_mult = is_rife and self._selected_rife_mode == "multiplier"
        self.rife_policy_row.setVisible(is_mult)

        show_custom_fps = (
            is_retime
            or (is_rife and not is_mult)
            or (is_mult and self.seg_rife_policy.currentKey() == "custom_fps")
        )
        self.custom_fps_panel.setVisible(show_custom_fps)
        if not self._syncing_ui:
            self._sync_simple_controls_from_state()
        self._update_summary()

    def _on_custom_fps_changed(self, val: float) -> None:
        for preset_val, btn in self._fps_chip_buttons:
            btn.setChecked(abs(val - preset_val) < 0.01)
        if not self._syncing_ui:
            self._sync_simple_controls_from_state()
        self._update_summary()

    def _fit_duration_under_3s(self) -> None:
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
        self._rife_models = list_available_rife_models()
        self.btn_dl_rife.setVisible(find_rife() is None)
        self.lbl_status.setText("RIFE 补帧引擎下载完成！")

    def _on_rife_download_failed(self, err: str) -> None:
        self.btn_dl_rife.setEnabled(True)
        self.lbl_status.setText(f"RIFE 下载失败: {err}")
        QMessageBox.warning(self, "下载 RIFE 失败", err)

    # ---------- Key Photo & Aspect Mismatch ----------

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
                self._ensure_crop_box_visible()
                self.preview.apply_aspect_fit(ratio, mode="inside")
                self._refresh_canvas_res_label()
            except Exception:
                pass
        self._update_summary()

    def _on_key_source_changed(self) -> None:
        is_video = self.seg_key_source.currentKey() == "video_frame"
        self.video_key_row_widget.setVisible(is_video)
        self.ext_key_container.setVisible(not is_video)
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
            self.seg_key_source.setCurrentKey("external_image")
            self._on_key_source_changed()
            if self.btn_ext_match.isChecked():
                self._on_ext_aspect_mode_clicked("match_cover_aspect")

    def _refresh_key_thumbnail(self) -> None:
        if not self.rb_key_video.isChecked() and self._external_key_path and self._external_key_path.is_file():
            pix = QPixmap(str(self._external_key_path))
            if not pix.isNull():
                self.lbl_key_thumb.setPixmap(
                    pix.scaled(82, 102, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
                return
        if self._video_info is not None:
            qimg = self._frame_provider.get_frame(self.spin_key_frame.value())
            if qimg is not None:
                pix = QPixmap.fromImage(qimg)
                self.lbl_key_thumb.setPixmap(
                    pix.scaled(82, 102, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
                return
        self.lbl_key_thumb.setText("封面")

    # ---------- Long Edge, Bitrate, EXIF (Folder Match), Naming & USB ----------

    def _on_long_edge_preset_clicked(self, val: int) -> None:
        self._syncing_ui = True
        try:
            self.spin_custom_edge.setValue(val)
            for k, eb in self._long_edge_buttons.items():
                eb.setChecked(k == val)
        finally:
            self._syncing_ui = False
        self._refresh_canvas_res_label()
        self._update_size_estimate()

    def _on_custom_edge_changed(self, val: int) -> None:
        if self._syncing_ui:
            return
        for k, eb in self._long_edge_buttons.items():
            eb.setChecked(k == val)
        self._refresh_canvas_res_label()
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
                self._exif_metadata.summary_text() if self._exif_metadata else "空白 (不写入相机参数)"
            )

    def _on_pick_folder_auto_exif(self) -> None:
        if self._video_info is None:
            return
        last_dir = str(self._settings.value("exif_search_dir", str(self._video_info.path.parent)))
        folder = QFileDialog.getExistingDirectory(
            self,
            f"选择包含 {self._video_info.path.stem} 同名 RAW / 照片的文件夹",
            last_dir,
        )
        if not folder:
            return
        self._settings.setValue("exif_search_dir", folder)
        match = find_matching_sidecar_exif_file(self._video_info.path, Path(folder))
        if match is None:
            QMessageBox.information(
                self,
                "未找到同名 RAW/照片",
                f"未在 {folder}（含一级子目录）下找到与「{self._video_info.path.stem}」同名的照片或 RAW。",
            )
            return
        try:
            meta = read_exif_from_file(match)
        except Exception as exc:
            QMessageBox.warning(self, "读取 EXIF 失败", str(exc))
            return
        self._exif_metadata = None if meta.is_empty() else meta
        self.lbl_exif_summary.setText(meta.summary_text())
        self.lbl_status.setText(f"已匹配导入 EXIF: {match.name}")

    def _on_folder_naming_changed(self) -> None:
        mode = str(self.seg_folder_naming.currentKey() or "suffix")
        self.edit_folder_suffix.setVisible(mode == "suffix")
        self.edit_folder_prefix.setVisible(mode in ("prefix_seq", "custom"))
        self.spin_folder_seq.setVisible(mode == "prefix_seq")
        self.lbl_fn_param.setVisible(mode != "original")
        if mode == "suffix":
            self.lbl_fn_param.setText("文件后缀")
        elif mode == "prefix_seq":
            self.lbl_fn_param.setText("前缀序号")
        elif mode == "custom":
            self.lbl_fn_param.setText("自定义名")
        self._update_export_preview()

    def _on_export_mode_changed(self) -> None:
        is_usb = self.seg_export_mode.currentKey() == "usb_dcim"
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
        job = self._build_job() if self._video_info is not None else None
        ext = "HEIC" if self.seg_img_fmt.currentKey() == "heic" else "JPG"
        if self.rb_export_usb.isChecked():
            root = str(self.combo_usb.currentData() or "").strip()
            rel_pair = (
                preview_output_filename(self._video_info, job)
                if (self._video_info is not None and job is not None)
                else f"DCIM/100APPLE/源文件名_2live.{ext} + .MOV"
            )
            if not root:
                self.lbl_usb_dest_preview.setText(f"输出预览: <U盘>/{rel_pair} (请插入U盘或自选目录)")
            else:
                self.lbl_usb_dest_preview.setText(f"输出预览: {Path(root) / rel_pair}")
        else:
            out_dir = Path(self.edit_folder.text().strip())
            if self._video_info is not None and job is not None:
                name_pair = preview_output_filename(self._video_info, job)
                self.lbl_folder_dest_preview.setText(f"输出预览: {out_dir / name_pair}")
            else:
                self.lbl_folder_dest_preview.setText(f"输出预览: {out_dir / f'源文件名_2live.{ext} + .MOV'}")

        if self._video_info is not None and job is not None:
            self.lbl_out_file_info.setText(f"输出名: {preview_output_filename(self._video_info, job)}")

    # ---------- Job Builder & Summary ----------

    def _effective_bitrate_mbps(self) -> float:
        if self._ui_mode == "simple":
            return IPHONE_LIVE_BITRATE_MBPS
        return float(self.slider_bitrate.value())

    def _build_job(self) -> ConversionJob:
        assert self._video_info is not None
        fps_key = str(self.seg_fps_mode.currentKey() or "keep_original")
        if fps_key == "rife_interpolate":
            fps_mode = (
                "ffmpeg_minterpolate"
                if (self._selected_rife_model == "ffmpeg_minterpolate" or find_rife() is None)
                else "rife_interpolate"
            )
        else:
            fps_mode = fps_key

        audio_key = str(self.seg_audio.currentKey() or "speed_sync")
        is_mute = audio_key == "mute"
        audio_retime_mode = "trim_fade" if audio_key == "trim_fade" else "speed_sync"

        is_usb = self.rb_export_usb.isChecked()
        usb_data = str(self.combo_usb.currentData() or "")
        out_root = Path(usb_data) if is_usb else Path(self.edit_folder.text().strip())

        is_simple = self._ui_mode == "simple"
        folder_mode = "suffix" if is_simple else str(self.seg_folder_naming.currentKey() or "suffix")
        folder_sfx = "_2live" if is_simple else (self.edit_folder_suffix.text().strip() or "_2live")

        return ConversionJob(
            video_path=self._video_info.path,
            start_frame=self.spin_start.value(),
            end_frame=self.spin_end.value(),
            crop=self.preview.get_crop(),
            padding=self.preview.get_padding(),
            polaroid_preset=self._active_polaroid,
            mute=is_mute,
            fps_mode=fps_mode,  # type: ignore[arg-type]
            custom_fps=self.spin_custom_fps.value(),
            rife_mode=self._selected_rife_mode,  # type: ignore[arg-type]
            rife_multiplier=max(2, self._selected_rife_mult) if self._selected_rife_mode == "multiplier" else 2,
            rife_model=self._selected_rife_model,
            rife_gpu_id=self._selected_rife_gpu,
            rife_playback_policy=str(self.seg_rife_policy.currentKey() or "keep_duration"),  # type: ignore[arg-type]
            audio_retime_mode=audio_retime_mode,  # type: ignore[arg-type]
            key_source="video_frame" if self.rb_key_video.isChecked() else "external_image",
            key_frame_index=self.spin_key_frame.value(),
            external_key_image=self._external_key_path,
            external_key_aspect_mode=self._selected_ext_aspect_mode(),  # type: ignore[arg-type]
            export_mode="usb_dcim" if is_usb else "folder",
            output_root=out_root,
            folder_naming_mode=folder_mode,  # type: ignore[arg-type]
            folder_suffix=folder_sfx,
            folder_custom_prefix=self.edit_folder_prefix.text().strip() or "LIVE_",
            folder_seq_number=self.spin_folder_seq.value(),
            avoid_overwrite=self.chk_avoid_overwrite.isChecked(),
            also_pack_livp=self.chk_livp.isChecked(),
            image_format=str(self.seg_img_fmt.currentKey() or "heic"),  # type: ignore[arg-type]
            video_max_edge=self.spin_custom_edge.value(),
            video_bitrate_mbps=self._effective_bitrate_mbps(),
            use_hw_encode=self.chk_hw_encode.isChecked(),
            bg_mode="blur" if self.btn_bg_blur.isChecked() else "solid",
            bg_blur_sigma=self.slider_blur.value(),
            bg_color=self._bg_color_hex,
            exif_metadata=self._exif_metadata,
        )

    def _build_job_for_state(self, st: QueueItemState) -> ConversionJob:
        is_usb = self.rb_export_usb.isChecked()
        usb_data = str(self.combo_usb.currentData() or "")
        out_root = Path(usb_data) if is_usb else Path(self.edit_folder.text().strip())
        fps_mode = st.fps_mode
        if fps_mode == "rife_interpolate" and (st.rife_model == "ffmpeg_minterpolate" or find_rife() is None):
            fps_mode = "ffmpeg_minterpolate"

        is_simple = self._ui_mode == "simple"
        folder_mode = "suffix" if is_simple else str(self.seg_folder_naming.currentKey() or "suffix")
        folder_sfx = "_2live" if is_simple else (self.edit_folder_suffix.text().strip() or "_2live")
        bitrate = IPHONE_LIVE_BITRATE_MBPS if is_simple else st.video_bitrate_mbps

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
            rife_multiplier=max(2, st.rife_multiplier),
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
            folder_naming_mode=folder_mode,  # type: ignore[arg-type]
            folder_suffix=folder_sfx,
            folder_custom_prefix=self.edit_folder_prefix.text().strip() or "LIVE_",
            folder_seq_number=self.spin_folder_seq.value(),
            avoid_overwrite=self.chk_avoid_overwrite.isChecked(),
            also_pack_livp=self.chk_livp.isChecked(),
            image_format=st.image_format,  # type: ignore[arg-type]
            video_max_edge=st.video_max_edge,
            video_bitrate_mbps=bitrate,
            use_hw_encode=self.chk_hw_encode.isChecked(),
            bg_mode=st.bg_mode,  # type: ignore[arg-type]
            bg_blur_sigma=st.bg_blur_sigma,
            bg_color=st.bg_color,
            exif_metadata=st.exif_metadata,
        )

    def _update_size_estimate(self) -> None:
        if self._video_info is None:
            self.lbl_est_size.setText("预计输出体积: -")
            self.lbl_out_spec_info.setText("输出规格: -")
            return
        job = self._build_job()
        summary = compute_timeline_summary(self._video_info, job)
        img_mb, mov_mb, tot_mb = estimate_output_size_mb(self._video_info, job, summary)
        self.lbl_est_size.setText(f"预计输出体积: ~{tot_mb:.1f} MB  (封面 {img_mb:.1f} MB + 视频 {mov_mb:.1f} MB)")

        c = self.preview.get_crop()
        pad = self.preview.get_padding()
        ow, oh = compute_scaled_video_dims(c.w + pad.left + pad.right, c.h + pad.top + pad.bottom, job.video_max_edge)
        out_dur_txt = format_frames_as_sec_frames(summary.output_frames, summary.output_fps)
        fps_int = int(round(summary.output_fps))
        mute_tag = "静音" if job.mute else "有声"
        self.lbl_out_spec_info.setText(
            f"输出规格: {ow}×{oh} · {fps_int} 帧/秒 · {summary.output_frames} 帧 ({out_dur_txt}) · {job.video_bitrate_mbps:.0f} Mbps (~{tot_mb:.1f} MB) · {mute_tag}"
        )
        self.lbl_out_file_info.setText(f"输出名: {preview_output_filename(self._video_info, job)}")

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
        self.btn_fit_3s_simple.setVisible(dur > 3.0)
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
        _, _, tot_mb = estimate_output_size_mb(self._video_info, job, s)

        summary_style = (
            f"background-color: {bg}; border: 1px solid {bd}; border-radius: 8px; padding: 7px 10px; color: {fg};"
        )
        adv_text = (
            f"截取: {s.selected_frames} 帧 ({in_dur_txt})  ➔  输出: {s.output_frames} 帧 @ {fps_int} 帧/秒\n"
            f"实况时长: {out_dur_txt}  |  封面锚点: {cover_txt}{note}"
        )
        sim_text = (
            f"输出: {s.output_frames} 帧 @ {fps_int} 帧/秒 ({out_dur_txt})  |  锚点: {cover_txt}\n"
            f"固定码率: {int(IPHONE_LIVE_BITRATE_MBPS)} Mbps (iPhone 实况参考)  ·  预计 ~{tot_mb:.1f} MB{note}"
        )
        self.lbl_summary.setStyleSheet(summary_style)
        self.lbl_summary.setText(adv_text)
        self.lbl_simple_summary.setStyleSheet(summary_style)
        self.lbl_simple_summary.setText(sim_text)

    # ---------- Context Menu & Shortcuts ----------

    def _on_preview_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        act_crop = menu.addAction("✓ 完成并隐藏裁切框" if self.preview.is_crop_editing() else "✂ 开启裁切框")
        act_crop.triggered.connect(
            lambda: self._on_framing_mode_changed("none" if self.preview.is_crop_editing() else "crop")
        )

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

        align_menu = menu.addMenu("✛ 裁切对齐")
        for lbl, m in (("居中", "center"), ("靠左", "left"), ("靠右", "right"), ("靠上", "top"), ("靠下", "bottom")):
            align_menu.addAction(lbl).triggered.connect(lambda _=False, mode=m: self._on_align_clicked(mode))

        pol_menu = menu.addMenu("🎞 拍立得相纸")
        for k, spec in POLAROID_PRESETS.items():
            pol_menu.addAction(spec.label).triggered.connect(lambda _=False, pk=k: self._apply_polaroid_preset(pk))
        pol_menu.addSeparator()
        pol_menu.addAction("重置为全画面").triggered.connect(self._reset_crop)

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

    def _check_rife_available_for_jobs(self, jobs: list[ConversionJob]) -> bool:
        need_rife = any(
            j.fps_mode == "ffmpeg_minterpolate"
            and str(self.seg_fps_mode.currentKey() or "") == "rife_interpolate"
            and j.rife_model != "ffmpeg_minterpolate"
            for j in jobs
        )
        if need_rife and find_rife() is None:
            QMessageBox.warning(
                self,
                "未检测到 RIFE 光流补帧引擎",
                "当前已选择「AI 光流补帧」，但未检测到 bin/rife/rife-ncnn-vulkan.exe。\n\n"
                "请先点击右侧「一键下载 RIFE」安装补帧引擎，或切换到「保持原帧率 / 原始帧重排」。",
            )
            return False
        return True

    def _on_start_convert(self) -> None:
        if self._video_info is None or not self._validate_export_target():
            return
        job = self._build_job()
        if not self._check_rife_available_for_jobs([job]):
            return
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
        jobs = [self._build_job_for_state(st) for st in self._queue]
        if not self._check_rife_available_for_jobs(jobs):
            return

        self.btn_convert.setEnabled(False)
        self.btn_convert_all.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_open_out.setEnabled(False)
        self.progress_bar.setValue(0)
        self.lbl_status.setText(f"正在启动批量转换 ({len(jobs)} 个视频)...")

        worker = BatchConversionWorker(jobs)
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

        if self._ui_mode == "advanced" and self.seg_folder_naming.currentKey() == "prefix_seq":
            self.spin_folder_seq.setValue((self.spin_folder_seq.value() % 9999) + 1)

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
            if self._ui_mode == "advanced" and self.seg_folder_naming.currentKey() == "prefix_seq":
                self.spin_folder_seq.setValue(((self.spin_folder_seq.value() + len(results) - 1) % 9999) + 1)
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
