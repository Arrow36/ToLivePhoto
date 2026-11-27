"""Application entry point for ToLivePhoto (supports windowless pythonw launch)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .ui.icon import create_app_icon
from .ui.main_window import MainWindow
from .ui.theme import ensure_bytedance_font


def main() -> int:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    ensure_bytedance_font(app)
    app.setApplicationName("ToLivePhoto")
    app.setOrganizationName("Arrow36")
    app.setWindowIcon(create_app_icon())
    initial_video = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    window = MainWindow(initial_video=initial_video)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
