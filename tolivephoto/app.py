"""Application entry point for ToLivePhoto (supports frozen EXE, pythonw launch, and --selftest)."""
from __future__ import annotations

import ctypes
import multiprocessing
import os
import sys
import tempfile
import uuid
from pathlib import Path

from PySide6.QtWidgets import QApplication

from tolivephoto.ui.icon import create_app_icon
from tolivephoto.ui.main_window import MainWindow
from tolivephoto.ui.theme import ensure_bytedance_font


def _run_selftest(report_path: Path | None) -> int:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PIL import Image

    from tolivephoto.core.apple_live import inspect_live_image, write_live_image
    from tolivephoto.core.tools import find_ffmpeg, find_ffprobe, find_rife, run_command

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    font_family = ensure_bytedance_font(app)
    icon = create_app_icon()
    assert not icon.pixmap(64, 64).isNull(), "App icon failed to load"

    win = MainWindow(initial_video=None)
    win.show()
    app.processEvents()
    win.close()
    app.processEvents()

    with tempfile.TemporaryDirectory() as td:
        heic_path = Path(td) / "selftest.HEIC"
        asset_id = str(uuid.uuid4()).upper()
        img = Image.new("RGB", (64, 64), (240, 240, 240))
        write_live_image(img, heic_path, asset_id)
        parsed_id = inspect_live_image(heic_path)
        assert parsed_id == asset_id, f"HEIC ContentIdentifier mismatch: {parsed_id} != {asset_id}"

    ffmpeg = find_ffmpeg()
    ffprobe = find_ffprobe()
    rife = find_rife()
    run_command([str(ffmpeg), "-version"])
    run_command([str(ffprobe), "-version"])
    if rife is not None:
        run_command([str(rife), "-l"], cwd=rife.parent, check=False)

    lines = [
        "SELFTEST_OK",
        f"font={font_family}",
        f"ffmpeg={ffmpeg}",
        f"ffprobe={ffprobe}",
        f"rife={rife}",
    ]
    output = "\n".join(lines)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(output, encoding="utf-8")
    print(output)
    return 0


def main() -> int:
    multiprocessing.freeze_support()
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        report = Path(sys.argv[2]) if len(sys.argv) >= 3 else None
        return _run_selftest(report)

    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Arrow36.ToLivePhoto.Studio")
        except Exception:
            pass

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
