---
name: desktop-studio-ui
description: PySide6 desktop creative studio UI/UX design guidelines for overflow-free inspector panels, progressive disclosure, and direct-manipulation canvases.
---

# Desktop Studio UI/UX Design Skill (PySide6)

Synthesized from `ui-ux-pro-max`, `frontend-design`, and `impeccable` principles adapted for high-DPI Windows desktop creative tools:

## 1. Overflow Immunity (Zero Horizontal Clipping)
- **ComboBoxes**: Always set `QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon` and `setMinimumContentsLength(6)` with `view().setTextElideMode(Qt.TextElideMode.ElideRight)` + full-text tooltip so long GPU or drive strings never inflate panel width.
- **Dynamic Strings**: Use `ElidedLabel` (`minimumWidth=40`, `Expanding` horizontal policy, full-text tooltip) for filenames, EXIF summaries, and export paths.
- **Row Budget**: Never cram more than 4–5 pill buttons into a single horizontal row inside a `500–620px` inspector; use a `QGridLayout` (`2×4` or `2×2`) for preset banks.
- **Scoped Transparent Containers**: Never apply raw `background: transparent;` without an `#objectName` selector on a container widget, or it will override child button QSS.

## 2. Spacing & Rhythm (4px / 8px Grid)
- Micro-gap (label ↔ control, segmented items): `4px`–`6px`.
- Row spacing inside cards: `8px`–`10px`.
- Card internal padding: `12px`–`14px`.
- Keep all cards' `minimumSizeHint().width() <= 480px` so a resizable `QSplitter` never clips content.

## 3. Progressive Disclosure & Hierarchy
- Use a **4-step Segmented Stepper** (`01 画面` / `02 帧率` / `03 封面` / `04 导出`) plus an optional **`展开全部`** toggle so single-focus editing requires zero scrolling.
- Pair the stepper with an always-visible **Live Summary Strip** whose chips jump directly to the relevant step.
- Hide the multi-file queue bar automatically when `<= 1` item is loaded.
- Anchor transport controls in 3 visual clusters (Left Trim, Center Hero Play, Right Primary Action) with a prominent `#heroPlayBtn`.

## 4. Direct-Manipulation Affordances
- Canvas: wheel zoom, left-drag pan, **double-click `100% ↔ 200%` zoom toggle**, and **hold-to-compare original frame**.
- Timeline: hover crosshair + frame/time tooltip, resize cursors on handles, clickable `★` cover pill to jump straight to the Key Photo.
