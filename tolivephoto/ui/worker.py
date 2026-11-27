"""Background QThread workers for single/batch Live Photo conversion and in-app RIFE setup."""
from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..core.interpolator import ConversionCancelled
from ..core.models import ConversionJob
from ..core.pipeline import ConversionResult, run_conversion
from ..core.rife_setup import install_rife
from ..core.tools import APP_DIR


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
    """Process a queue of ConversionJobs sequentially with auto-incrementing DCF/folder numbers."""

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
        next_seq: int | None = None
        try:
            for idx, job in enumerate(self._jobs):
                if self._cancel_requested:
                    raise ConversionCancelled("用户已取消转换")
                if job.export_mode == "usb_dcim" and job.dcf_number is not None and next_dcf is not None:
                    job.dcf_number = next_dcf
                elif job.dcf_number is None and job.folder_naming_mode == "prefix_seq" and next_seq is not None:
                    job.folder_seq_number = next_seq

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
                if job.dcf_number is None and job.folder_naming_mode == "prefix_seq":
                    next_seq = job.folder_seq_number + 1

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
        try:
            self.progress.emit("正在下载 RIFE 补帧引擎 (~22 MB)...")
            rc = install_rife(APP_DIR / "bin" / "rife")
            if rc != 0:
                self.failed.emit(f"RIFE 安装失败 (exit={rc})")
                return
            self.succeeded.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
