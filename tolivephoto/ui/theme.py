"""60-30-10 Pastel Studio color palette, DPI font scaling, & studio QSS stylesheet for ToLivePhoto."""
from __future__ import annotations

from PySide6.QtWidgets import QApplication

PALETTE = {
    "window_bg": "#EEF2FA",
    "card_bg": "#FEFDF9",
    "surface_subtle": "#F4F6FC",
    "surface_recessed": "#E7ECF7",
    "canvas_porcelain": "#FEFDF9",
    "canvas_light": "#E6ECF8",
    "canvas_dark": "#1E2130",
    "sky": "#A4C6E9",
    "lavender": "#B8AEE3",
    "lavender_soft": "#EBE8FB",
    "mint": "#C7F3F6",
    "mint_soft": "#E9FAFC",
    "indigo": "#7D85CC",
    "indigo_hover": "#6B74BE",
    "indigo_pressed": "#5962A8",
    "sakura": "#FADCFE",
    "sakura_border": "#DF9FE8",
    "sakura_ink": "#642E6E",
    "ink_primary": "#25293A",
    "ink_secondary": "#4F5776",
    "ink_muted": "#7C85A5",
    "border_soft": "#D4E0F4",
}


def compute_dpi_font_scale(app: QApplication | None = None) -> float:
    """Scale UI typography slightly upward on high-DPI displays (e.g. 4K 150%-200%)."""
    if app is None:
        app = QApplication.instance()
    if app is None:
        return 1.12
    screen = app.primaryScreen()
    if screen is None:
        return 1.12
    dpr = float(screen.devicePixelRatio() or 1.0)
    logical_dpi = float(screen.logicalDotsPerInch() or 96.0)
    effective = max(dpr, logical_dpi / 96.0)
    if effective >= 1.75:
        return 1.18
    if effective >= 1.35:
        return 1.12
    return 1.05


def build_pastel_stylesheet(scale: float = 1.12) -> str:
    base_px = max(12, int(round(12 * scale)))
    small_px = max(11, int(round(11 * scale)))
    title_px = max(13, int(round(13.5 * scale)))
    cta_px = max(13, int(round(13.5 * scale)))
    input_h = max(24, int(round(22 * scale)))

    return f"""
QMainWindow, QWidget {{
    background-color: {PALETTE["window_bg"]};
    color: {PALETTE["ink_primary"]};
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: {base_px}px;
}}
QDialog {{
    background-color: {PALETTE["card_bg"]};
    color: {PALETTE["ink_primary"]};
}}
QWidget#queueChipsHost {{
    background-color: transparent;
}}
QFrame#brandHeader, QFrame#sectionCard, QFrame#stageCard, QFrame#exportFooter, QFrame#queueBar, QFrame#liveSummaryBar {{
    background-color: {PALETTE["card_bg"]};
    border: 1px solid {PALETTE["border_soft"]};
    border-radius: 12px;
}}
QFrame#navStepBar {{
    background-color: {PALETTE["card_bg"]};
    border: 1px solid {PALETTE["border_soft"]};
    border-radius: 10px;
}}
QFrame#segmentedBar {{
    background-color: {PALETTE["surface_recessed"]};
    border: 1px solid {PALETTE["border_soft"]};
    border-radius: 9px;
}}
QLabel {{
    background-color: transparent;
}}
QLabel#fieldLabel {{
    color: {PALETTE["ink_secondary"]};
    font-weight: 600;
    font-size: {small_px}px;
}}
QLabel#sectionBadge {{
    background-color: {PALETTE["lavender_soft"]};
    color: {PALETTE["indigo"]};
    border: 1px solid {PALETTE["lavender"]};
    border-radius: 6px;
    padding: 2px 7px;
    font-weight: bold;
    font-size: {small_px}px;
}}
QLabel#sectionTitle {{
    color: {PALETTE["ink_primary"]};
    font-weight: bold;
    font-size: {title_px}px;
}}
QLabel#livePill {{
    background-color: {PALETTE["sakura"]};
    color: {PALETTE["sakura_ink"]};
    border: 1px solid {PALETTE["sakura_border"]};
    border-radius: 8px;
    padding: 2px 9px;
    font-weight: bold;
    font-size: {small_px}px;
}}
QLabel#metaCapsule {{
    background-color: {PALETTE["surface_subtle"]};
    color: {PALETTE["ink_secondary"]};
    border: 1px solid {PALETTE["border_soft"]};
    border-radius: 8px;
    padding: 4px 10px;
}}
QSplitter::handle {{
    background: transparent;
    width: 6px;
}}
QSplitter::handle:hover {{
    background: {PALETTE["sky"]};
    border-radius: 3px;
}}
QPushButton {{
    background-color: {PALETTE["surface_subtle"]};
    border: 1px solid {PALETTE["border_soft"]};
    border-radius: 7px;
    padding: 5px 10px;
    color: {PALETTE["ink_primary"]};
}}
QPushButton:hover {{
    background-color: #EAEFFA;
    border-color: {PALETTE["sky"]};
}}
QPushButton:pressed {{
    background-color: #DEE7F7;
}}
QPushButton:disabled {{
    color: {PALETTE["ink_muted"]};
    background-color: #F2F4FA;
    border-color: #E5E9F4;
}}
QPushButton#segBtn {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 5px 8px;
    color: {PALETTE["ink_secondary"]};
    font-size: {small_px}px;
}}
QPushButton#segBtn:hover {{
    color: {PALETTE["ink_primary"]};
    background-color: rgba(254, 253, 249, 0.65);
}}
QPushButton#segBtn:checked {{
    background-color: {PALETTE["card_bg"]};
    border: 1px solid {PALETTE["sky"]};
    color: {PALETTE["indigo_pressed"]};
    font-weight: bold;
}}
QPushButton#navStepBtn {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 6px 8px;
    color: {PALETTE["ink_secondary"]};
    font-weight: 600;
    font-size: {small_px}px;
}}
QPushButton#navStepBtn:hover {{
    background-color: {PALETTE["surface_subtle"]};
    color: {PALETTE["ink_primary"]};
}}
QPushButton#navStepBtn:checked {{
    background-color: {PALETTE["indigo"]};
    border-color: {PALETTE["indigo"]};
    color: {PALETTE["card_bg"]};
    font-weight: bold;
}}
QPushButton#summaryPillBtn {{
    background-color: {PALETTE["surface_subtle"]};
    border: 1px solid {PALETTE["border_soft"]};
    border-radius: 7px;
    padding: 4px 8px;
    color: {PALETTE["ink_secondary"]};
    font-size: {small_px}px;
    text-align: left;
}}
QPushButton#summaryPillBtn:hover {{
    border-color: {PALETTE["indigo"]};
    background-color: {PALETTE["lavender_soft"]};
    color: {PALETTE["ink_primary"]};
}}
QPushButton#heroPlayBtn {{
    background-color: {PALETTE["indigo"]};
    border: 1px solid {PALETTE["indigo"]};
    border-radius: 8px;
    padding: 6px 14px;
    color: {PALETTE["card_bg"]};
    font-weight: bold;
}}
QPushButton#heroPlayBtn:hover {{
    background-color: {PALETTE["indigo_hover"]};
}}
QPushButton#heroPlayBtn:pressed {{
    background-color: {PALETTE["indigo_pressed"]};
}}
QPushButton#modeOptionBtn {{
    text-align: left;
    padding: 6px 10px;
    border-radius: 8px;
    background-color: {PALETTE["surface_subtle"]};
    border: 1px solid {PALETTE["border_soft"]};
    color: {PALETTE["ink_secondary"]};
}}
QPushButton#modeOptionBtn:hover {{
    background-color: #EFECFB;
    border-color: {PALETTE["lavender"]};
    color: {PALETTE["ink_primary"]};
}}
QPushButton#modeOptionBtn:checked {{
    background-color: {PALETTE["lavender_soft"]};
    border: 2px solid {PALETTE["indigo"]};
    color: {PALETTE["ink_primary"]};
    font-weight: bold;
}}
QPushButton#chipBtn {{
    background-color: {PALETTE["surface_subtle"]};
    border: 1px solid {PALETTE["border_soft"]};
    border-radius: 7px;
    padding: 5px 8px;
    color: {PALETTE["ink_secondary"]};
    font-size: {small_px}px;
}}
QPushButton#chipBtn:hover {{
    border-color: {PALETTE["sky"]};
    background-color: #EAF3FC;
    color: {PALETTE["ink_primary"]};
}}
QPushButton#chipBtn:checked {{
    background-color: {PALETTE["indigo"]};
    border: 1px solid {PALETTE["indigo"]};
    color: {PALETTE["card_bg"]};
    font-weight: bold;
}}
QPushButton#fpsChipBtn {{
    background-color: {PALETTE["surface_subtle"]};
    border: 1px solid {PALETTE["border_soft"]};
    border-radius: 7px;
    padding: 5px 6px;
    color: {PALETTE["ink_secondary"]};
    font-weight: bold;
}}
QPushButton#fpsChipBtn:hover {{
    border-color: {PALETTE["sky"]};
    background-color: #EAF3FC;
}}
QPushButton#fpsChipBtn:checked {{
    background-color: {PALETTE["indigo"]};
    border: 1px solid {PALETTE["indigo"]};
    color: {PALETTE["card_bg"]};
}}
QPushButton#primaryBtn {{
    background-color: {PALETTE["indigo"]};
    border: 1px solid {PALETTE["indigo"]};
    border-radius: 9px;
    font-weight: bold;
    font-size: {cta_px}px;
    padding: 9px 16px;
    color: {PALETTE["card_bg"]};
}}
QPushButton#primaryBtn:hover {{
    background-color: {PALETTE["indigo_hover"]};
}}
QPushButton#primaryBtn:pressed {{
    background-color: {PALETTE["indigo_pressed"]};
}}
QPushButton#primaryBtn:disabled {{
    background-color: #C4C9E6;
    border-color: #C4C9E6;
    color: #F6F7FD;
}}
QPushButton#keyStarBtn {{
    background-color: {PALETTE["sakura"]};
    border: 1px solid {PALETTE["sakura_border"]};
    color: {PALETTE["sakura_ink"]};
    font-weight: bold;
    border-radius: 7px;
    padding: 5px 10px;
}}
QPushButton#keyStarBtn:hover {{
    background-color: #F6C8F9;
}}
QPushButton#cropToggleBtn {{
    background-color: {PALETTE["card_bg"]};
    border: 1px solid {PALETTE["sky"]};
    color: {PALETTE["ink_primary"]};
    font-weight: bold;
    border-radius: 7px;
}}
QPushButton#cropToggleBtn:checked {{
    background-color: {PALETTE["indigo"]};
    border: 1px solid {PALETTE["indigo"]};
    color: {PALETTE["card_bg"]};
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {PALETTE["card_bg"]};
    border: 1px solid {PALETTE["sky"]};
    border-radius: 7px;
    padding: 4px 8px;
    color: {PALETTE["ink_primary"]};
    min-height: {input_h}px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1.5px solid {PALETTE["indigo"]};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
    background: transparent;
}}
QComboBox QAbstractItemView {{
    background-color: {PALETTE["card_bg"]};
    color: {PALETTE["ink_primary"]};
    selection-background-color: {PALETTE["lavender_soft"]};
    selection-color: {PALETTE["ink_primary"]};
    border: 1px solid {PALETTE["sky"]};
    outline: none;
    padding: 3px;
}}
QComboBox QAbstractItemView::item {{
    min-height: {input_h}px;
    padding: 3px 8px;
    border-radius: 5px;
}}
QSlider::groove:horizontal {{
    border: none;
    height: 6px;
    background: {PALETTE["border_soft"]};
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: {PALETTE["indigo"]};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {PALETTE["card_bg"]};
    border: 2px solid {PALETTE["indigo"]};
    width: 15px;
    height: 15px;
    margin: -5px 0;
    border-radius: 8px;
}}
QProgressBar {{
    background-color: {PALETTE["surface_subtle"]};
    border: 1px solid {PALETTE["border_soft"]};
    border-radius: 7px;
    text-align: center;
    color: {PALETTE["ink_primary"]};
    font-weight: bold;
    height: 20px;
}}
QProgressBar::chunk {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {PALETTE["indigo"]}, stop:1 {PALETTE["sky"]});
    border-radius: 6px;
}}
QMenu {{
    background-color: {PALETTE["card_bg"]};
    border: 1px solid {PALETTE["sky"]};
    border-radius: 8px;
    padding: 4px;
    color: {PALETTE["ink_primary"]};
}}
QMenu::item {{
    padding: 6px 20px 6px 12px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background-color: {PALETTE["lavender_soft"]};
    color: {PALETTE["ink_primary"]};
}}
QMenu::separator {{
    height: 1px;
    background: {PALETTE["border_soft"]};
    margin: 4px 8px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    margin: 2px 0;
}}
QScrollBar::handle:vertical {{
    background: {PALETTE["sky"]};
    border-radius: 3px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {PALETTE["indigo"]};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""


PASTEL_STYLESHEET = build_pastel_stylesheet(1.12)
