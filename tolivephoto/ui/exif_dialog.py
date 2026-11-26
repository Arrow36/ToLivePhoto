"""EXIF metadata editor & RAW/photo EXIF importer dialog for ToLivePhoto."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..core.exif_io import ExifMetadata, find_matching_sidecar_exif_file, read_exif_from_file
from .theme import PALETTE


class ExifEditorDialog(QDialog):
    """Modal editor to inspect, edit, or import EXIF from RAW (.CR3/.NEF/.ARW/.DNG/.RAF) & photos."""

    def __init__(
        self,
        initial: ExifMetadata | None = None,
        video_path: Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("相机 EXIF 参数编辑与导入")
        self.resize(620, 560)
        self._video_path = video_path
        self._meta = initial.clone() if initial is not None else ExifMetadata()
        self._build_ui()
        self._load_meta_to_fields(self._meta)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)
        self.btn_import = QPushButton("📥 从照片 / RAW 导入 EXIF...")
        self.btn_import.setObjectName("cropToggleBtn")
        self.btn_import.clicked.connect(self._on_import_file)
        self.btn_auto_match = QPushButton("🪄 自动匹配同名 RAW/照片")
        self.btn_auto_match.clicked.connect(self._on_auto_match)
        self.btn_auto_match.setEnabled(self._video_path is not None)
        self.btn_clear = QPushButton("清空还原默认")
        self.btn_clear.clicked.connect(self._on_clear)
        top_bar.addWidget(self.btn_import)
        top_bar.addWidget(self.btn_auto_match)
        top_bar.addStretch(1)
        top_bar.addWidget(self.btn_clear)
        root.addLayout(top_bar)

        hint = QLabel(
            "保留 Apple Live Photo 实况配对标识 (MakerNote UUID) 的同时，将相机型号、镜头、快门、光圈、ISO "
            "与拍摄时间写入实况照片，iPhone 相册上滑即可查看完整参数。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"background-color: {PALETTE['mint_soft']}; border: 1px solid {PALETTE['sky']}; "
            f"border-radius: 8px; padding: 7px 10px; color: {PALETTE['ink_secondary']};"
        )
        root.addWidget(hint)

        cols = QHBoxLayout()
        cols.setSpacing(14)
        form_l = QFormLayout()
        form_r = QFormLayout()
        form_l.setSpacing(8)
        form_r.setSpacing(8)

        self.ed_make = QLineEdit()
        self.ed_make.setPlaceholderText("例如: Canon")
        self.ed_model = QLineEdit()
        self.ed_model.setPlaceholderText("例如: Canon PowerShot G5 X Mark II")
        self.ed_lens = QLineEdit()
        self.ed_lens.setPlaceholderText("例如: 8.8-44.0mm f/1.8-2.8")
        self.ed_dt = QLineEdit()
        self.ed_dt.setPlaceholderText("YYYY:MM:DD HH:MM:SS")
        self.ed_offset = QLineEdit()
        self.ed_offset.setPlaceholderText("+08:00")
        self.ed_software = QLineEdit()
        self.ed_software.setPlaceholderText("留空使用默认")
        self.ed_artist = QLineEdit()
        self.ed_copyright = QLineEdit()

        form_l.addRow("相机厂商:", self.ed_make)
        form_l.addRow("相机型号:", self.ed_model)
        form_l.addRow("镜头型号:", self.ed_lens)
        form_l.addRow("拍摄时间:", self.ed_dt)
        form_l.addRow("时区偏移:", self.ed_offset)
        form_l.addRow("软件版本:", self.ed_software)
        form_l.addRow("拍摄作者:", self.ed_artist)
        form_l.addRow("版权信息:", self.ed_copyright)

        self.ed_exposure = QLineEdit()
        self.ed_exposure.setPlaceholderText("例如: 1/250")
        self.ed_fnumber = QLineEdit()
        self.ed_fnumber.setPlaceholderText("例如: 5.6")
        self.ed_iso = QLineEdit()
        self.ed_iso.setPlaceholderText("例如: 125")
        self.ed_focal = QLineEdit()
        self.ed_focal.setPlaceholderText("例如: 21.6")
        self.ed_focal35 = QLineEdit()
        self.ed_focal35.setPlaceholderText("例如: 50")
        self.ed_ev = QLineEdit()
        self.ed_ev.setPlaceholderText("例如: -1/3")
        self.ed_lat = QLineEdit()
        self.ed_lat.setPlaceholderText("例如: 31.3041")
        self.ed_lon = QLineEdit()
        self.ed_lon.setPlaceholderText("例如: 120.6195")

        form_r.addRow("快门速度:", self.ed_exposure)
        form_r.addRow("光圈 F值:", self.ed_fnumber)
        form_r.addRow("感光度 ISO:", self.ed_iso)
        form_r.addRow("焦距 (mm):", self.ed_focal)
        form_r.addRow("等效 35mm:", self.ed_focal35)
        form_r.addRow("曝光补偿:", self.ed_ev)
        form_r.addRow("GPS 纬度:", self.ed_lat)
        form_r.addRow("GPS 经度:", self.ed_lon)

        cols.addLayout(form_l, 1)
        cols.addLayout(form_r, 1)
        root.addLayout(cols, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("应用 EXIF")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _load_meta_to_fields(self, m: ExifMetadata) -> None:
        self.ed_make.setText(m.make)
        self.ed_model.setText(m.model)
        self.ed_lens.setText(m.lens_model)
        self.ed_dt.setText(m.datetime_original)
        self.ed_offset.setText(m.offset_time or "+08:00")
        self.ed_software.setText(m.software)
        self.ed_artist.setText(m.artist)
        self.ed_copyright.setText(m.copyright)

        self.ed_exposure.setText(m.exposure_time)
        self.ed_fnumber.setText(m.f_number)
        self.ed_iso.setText("" if m.iso is None else str(m.iso))
        self.ed_focal.setText(m.focal_length)
        self.ed_focal35.setText("" if m.focal_length_35mm is None else str(m.focal_length_35mm))
        self.ed_ev.setText(m.exposure_bias)
        self.ed_lat.setText("" if m.gps_lat is None else f"{m.gps_lat:.6f}")
        self.ed_lon.setText("" if m.gps_lon is None else f"{m.gps_lon:.6f}")

    def _on_import_file(self) -> None:
        start_dir = str(self._video_path.parent) if self._video_path else str(Path.home() / "Desktop")
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "选择要读取 EXIF 的照片或 RAW 文件",
            start_dir,
            "RAW 与图片 (*.cr3 *.cr2 *.nef *.arw *.dng *.raf *.orf *.rw2 *.jpg *.jpeg *.heic *.heif *.tif *.tiff *.png *.webp);;所有文件 (*.*)",
        )
        if not path_str:
            return
        try:
            meta = read_exif_from_file(Path(path_str))
        except Exception as exc:
            QMessageBox.warning(self, "读取 EXIF 失败", str(exc))
            return
        if meta.is_empty():
            QMessageBox.information(self, "未检测到 EXIF", "该文件中未包含可识别的相机 EXIF 信息。")
            return
        self._load_meta_to_fields(meta)

    def _on_auto_match(self) -> None:
        if self._video_path is None:
            return
        match = find_matching_sidecar_exif_file(self._video_path)
        if match is None:
            QMessageBox.information(self, "未找到同名文件", f"未在 {self._video_path.parent} 下找到同名的 RAW 或照片文件。")
            return
        try:
            meta = read_exif_from_file(match)
        except Exception as exc:
            QMessageBox.warning(self, "读取 EXIF 失败", str(exc))
            return
        self._load_meta_to_fields(meta)

    def _on_clear(self) -> None:
        self._load_meta_to_fields(ExifMetadata())

    def get_exif_metadata(self) -> ExifMetadata | None:
        def _opt_int(text: str) -> int | None:
            t = text.strip()
            if not t:
                return None
            try:
                return int(float(t))
            except ValueError:
                return None

        def _opt_float(text: str) -> float | None:
            t = text.strip()
            if not t:
                return None
            try:
                return float(t)
            except ValueError:
                return None

        meta = ExifMetadata(
            make=self.ed_make.text().strip(),
            model=self.ed_model.text().strip(),
            lens_model=self.ed_lens.text().strip(),
            software=self.ed_software.text().strip(),
            datetime_original=self.ed_dt.text().strip(),
            offset_time=self.ed_offset.text().strip() or "+08:00",
            exposure_time=self.ed_exposure.text().strip(),
            f_number=self.ed_fnumber.text().strip(),
            iso=_opt_int(self.ed_iso.text()),
            focal_length=self.ed_focal.text().strip(),
            focal_length_35mm=_opt_int(self.ed_focal35.text()),
            exposure_bias=self.ed_ev.text().strip(),
            artist=self.ed_artist.text().strip(),
            copyright=self.ed_copyright.text().strip(),
            gps_lat=_opt_float(self.ed_lat.text()),
            gps_lon=_opt_float(self.ed_lon.text()),
        )
        return None if meta.is_empty() else meta
