"""Background QThread workers for single/batch Live Photo conversion and in-app RIFE setup."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..core.interpolator import ConversionCancelled
from ..core.models import ConversionJob
from ..core.pipeline import ConversionResult, run_conversion
from ..core.tools import PROJECT_ROOT, _creation_flags


class ConversionWorker(QThread):
    progress = Signal(int, str)
    succeeded = Signal(object)
    cancelled = Signal()
    failed = Signal(str)

    def __init__(self, job: ConversionJob) -> None:
        super().__init__()
        self._job = job
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        try:
            result: ConversionResult = run_conversion(
                self._job,
                progress_cb=lambda pct, msg: self.progress.emit(pct, msg),
                cancel_cb=lambda: self._cancel_requested,
            )
            self.succeeded.emit(result)
        except ConversionCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class BatchConversionWorker(QThread):
    """Process a queue of ConversionJobs sequentially with auto-incrementing DCF numbers."""

    itemStarted = Signal(int, int, str)
    progress = Signal(int, str)
    itemSucceeded = Signal(int, object)
    allSucceeded = Signal(list)
    cancelled = Signal()
    failed = Signal(str)

    def __init__(self, jobs: list[ConversionJob], auto_inc_dcf: bool = True) -> None:
        super().__init__()
        self._jobs = jobs
        self._auto_inc_dcf = auto_inc_dcf
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        results: list[ConversionResult] = []
        total = len(self._jobs)
        next_dcf: int | None = None
        try:
            for idx, job in enumerate(self._jobs):
                if self._cancel_requested:
                    raise ConversionCancelled("用户已取消批量转换")
                if job.export_mode == "usb_dcim" and next_dcf is not None:
                    job.dcf_number = next_dcf

                self.itemStarted.emit(idx, total, job.video_path.name)

                def _on_prog(pct: int, msg: str, i: int = idx, name: str = job.video_path.name) -> None:
                    overall = int(((i + pct / 100.0) / max(1, total)) * 100.0)
                    self.progress.emit(overall, f"[{i + 1}/{total} {name}] {msg}")

                res = run_conversion(
                    job,
                    progress_cb=_on_prog,
                    cancel_cb=lambda: self._cancel_requested,
                )
                results.append(res)
                self.itemSucceeded.emit(idx, res)
                if res.used_dcf_number is not None and self._auto_inc_dcf:
                    next_dcf = (res.used_dcf_number % 9999) + 1

            self.allSucceeded.emit(results)
        except ConversionCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class RifeDownloadWorker(QThread):
    progress = Signal(str)
    succeeded = Signal()
    failed = Signal(str)

    def run(self) -> None:
        script = Path(PROJECT_ROOT) / "scripts" / "setup_rife.py"
        if not script.is_file():
            self.failed.emit(f"未找到下载脚本: {script}")
            return
        try:
            self.progress.emit("正在下载 RIFE 补帧引擎 (~22 MB)...")
            proc = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_creation_flags(),
            )
            if proc.returncode != 0:
                self.failed.emit(proc.stderr.strip() or proc.stdout.strip() or "下载失败")
                return
            self.succeeded.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
