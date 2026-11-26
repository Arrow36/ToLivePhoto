"""Application entry point for ToLivePhoto."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .ui.icon import create_app_icon
from .ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("ToLivePhoto")
    app.setOrganizationName("Arrow36")
    app.setWindowIcon(create_app_icon())
    initial_video = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    window = MainWindow(initial_video=initial_video)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
