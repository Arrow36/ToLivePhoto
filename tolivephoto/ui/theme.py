"""60-30-10 Pastel Studio color palette & QSS stylesheet for ToLivePhoto."""
from __future__ import annotations

# User palette swatches:
#   #FEFDF9  Porcelain Ivory   (60% Dominant Cards & Header)
#   #A4C6E9  Sky Blue          (30% Structural Borders, Timeline Active Range, Secondary Tints)
#   #B8AEE3  Wisteria Lavender (30% Selected Option Pill Fill & Hover Tint)
#   #C7F3F6  Ice Mint          (30% Summary Metrics Card Tint)
#   #838BCC  Periwinkle Indigo (10% Primary CTA, Active Crop Frame, Progress, Checked Indicator)
#   #FADCFE  Sakura Orchid     (10% Jewel Highlights: ★ Cover, LIVE Badge, >3s Alert Pill)

PALETTE = {
    "window_bg": "#F2F5FC",
    "card_bg": "#FEFDF9",
    "surface_subtle": "#F6F7FC",
    "canvas_light": "#E6ECF8",
    "canvas_dark": "#1E2130",
    "sky": "#A4C6E9",
    "lavender": "#B8AEE3",
    "lavender_soft": "#EBE9FA",
    "mint": "#C7F3F6",
    "mint_soft": "#ECFAFC",
    "indigo": "#838BCC",
    "indigo_hover": "#7079BE",
    "indigo_pressed": "#5E67A8",
    "sakura": "#FADCFE",
    "sakura_border": "#E4AEEC",
    "sakura_ink": "#6B3475",
    "ink_primary": "#2D3142",
    "ink_secondary": "#5C6484",
    "ink_muted": "#8891B0",
    "border_soft": "#DCE6F7",
}

PASTEL_STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {PALETTE["window_bg"]};
    color: {PALETTE["ink_primary"]};
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 12px;
}}
QFrame#brandHeader, QFrame#sectionCard, QFrame#stageCard, QFrame#exportFooter {{
    background-color: {PALETTE["card_bg"]};
    border: 1px solid {PALETTE["border_soft"]};
    border-radius: 12px;
}}
QFrame#brandHeader QWidget, QFrame#sectionCard QWidget, QFrame#stageCard QWidget, QFrame#exportFooter QWidget {{
    background-color: transparent;
}}
QLabel#sectionBadge {{
    background-color: {PALETTE["lavender_soft"]};
    color: {PALETTE["indigo"]};
    border: 1px solid {PALETTE["lavender"]};
    border-radius: 6px;
    padding: 1px 6px;
    font-weight: bold;
    font-size: 11px;
}}
QLabel#sectionTitle {{
    color: {PALETTE["ink_primary"]};
    font-weight: bold;
    font-size: 13px;
}}
QLabel#livePill {{
    background-color: {PALETTE["sakura"]};
    color: {PALETTE["sakura_ink"]};
    border: 1px solid {PALETTE["sakura_border"]};
    border-radius: 8px;
    padding: 2px 8px;
    font-weight: bold;
    font-size: 11px;
}}
QLabel#metaCapsule {{
    background-color: {PALETTE["surface_subtle"]};
    color: {PALETTE["ink_secondary"]};
    border: 1px solid {PALETTE["border_soft"]};
    border-radius: 8px;
    padding: 4px 10px;
}}
QPushButton {{
    background-color: {PALETTE["surface_subtle"]};
    border: 1px solid {PALETTE["border_soft"]};
    border-radius: 7px;
    padding: 5px 10px;
    color: {PALETTE["ink_primary"]};
}}
QPushButton:hover {{
    background-color: #EEF2FB;
    border-color: {PALETTE["sky"]};
}}
QPushButton:pressed {{
    background-color: #E3EAF8;
}}
QPushButton:disabled {{
    color: {PALETTE["ink_muted"]};
    background-color: #F4F5FA;
    border-color: #E7EBF5;
}}
QPushButton#modeOptionBtn {{
    text-align: left;
    padding: 5px 10px;
    border-radius: 8px;
    background-color: {PALETTE["surface_subtle"]};
    border: 1px solid {PALETTE["border_soft"]};
    color: {PALETTE["ink_secondary"]};
}}
QPushButton#modeOptionBtn:hover {{
    background-color: #F1EFFB;
    border-color: {PALETTE["lavender"]};
    color: {PALETTE["ink_primary"]};
}}
QPushButton#modeOptionBtn:checked {{
    background-color: {PALETTE["lavender_soft"]};
    border: 2px solid {PALETTE["indigo"]};
    color: {PALETTE["ink_primary"]};
    font-weight: bold;
}}
QPushButton#fpsChipBtn {{
    background-color: {PALETTE["surface_subtle"]};
    border: 1px solid {PALETTE["border_soft"]};
    border-radius: 6px;
    padding: 4px 4px;
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
    font-size: 13px;
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
    background-color: #C7CBE8;
    border-color: #C7CBE8;
    color: #F5F6FC;
}}
QPushButton#keyStarBtn {{
    background-color: {PALETTE["sakura"]};
    border: 1px solid {PALETTE["sakura_border"]};
    color: {PALETTE["sakura_ink"]};
    font-weight: bold;
    border-radius: 7px;
}}
QPushButton#keyStarBtn:hover {{
    background-color: #F6CAF9;
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
    border-radius: 6px;
    padding: 4px 8px;
    color: {PALETTE["ink_primary"]};
    min-height: 22px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1.5px solid {PALETTE["indigo"]};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background-color: {PALETTE["card_bg"]};
    color: {PALETTE["ink_primary"]};
    selection-background-color: {PALETTE["lavender_soft"]};
    selection-color: {PALETTE["ink_primary"]};
    border: 1px solid {PALETTE["sky"]};
}}
QProgressBar {{
    background-color: {PALETTE["surface_subtle"]};
    border: 1px solid {PALETTE["border_soft"]};
    border-radius: 6px;
    text-align: center;
    color: {PALETTE["ink_primary"]};
    font-weight: bold;
    height: 18px;
}}
QProgressBar::chunk {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {PALETTE["indigo"]}, stop:1 {PALETTE["sky"]});
    border-radius: 5px;
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
