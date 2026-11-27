"""Build standalone Windows x64 ToLivePhoto distribution (ToLivePhoto.exe + _internal) and zip archive."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tolivephoto.core.rife_setup import install_rife
from tolivephoto.core.tools import find_ffmpeg, find_ffprobe

VERSION = "2.5.0"
BTBN_SHARED_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-gpl-shared.zip"
)


def _ensure_ffmpeg_bin(bin_ffmpeg: Path) -> None:
    ffmpeg_exe = bin_ffmpeg / "ffmpeg.exe"
    ffprobe_exe = bin_ffmpeg / "ffprobe.exe"
    if ffmpeg_exe.is_file() and ffprobe_exe.is_file():
        print(f"FFmpeg already present in {bin_ffmpeg}")
        return

    bin_ffmpeg.mkdir(parents=True, exist_ok=True)
    tmp_zip = ROOT_DIR / "build" / "ffmpeg-gpl-shared.zip"
    tmp_zip.parent.mkdir(parents=True, exist_ok=True)
    try:
        print(f"Downloading shared FFmpeg (~88 MB) from {BTBN_SHARED_URL} ...")
        req = urllib.request.Request(BTBN_SHARED_URL, headers={"User-Agent": "ToLivePhoto-builder"})
        with urllib.request.urlopen(req, timeout=180) as resp, tmp_zip.open("wb") as out:
            shutil.copyfileobj(resp, out, length=1024 * 1024)
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            for member in zf.infolist():
                norm = member.filename.replace("\\", "/")
                if "/bin/" not in norm or norm.endswith("/"):
                    continue
                fname = Path(norm).name
                if fname.lower().endswith((".exe", ".dll")):
                    with zf.open(member) as src, (bin_ffmpeg / fname).open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        if ffmpeg_exe.is_file() and ffprobe_exe.is_file():
            print(f"Extracted shared FFmpeg into {bin_ffmpeg}")
            return
    except Exception as exc:
        print(f"Shared FFmpeg download failed ({exc}); falling back to system FFmpeg...")
    finally:
        if tmp_zip.exists():
            tmp_zip.unlink(missing_ok=True)

    src_ffmpeg = find_ffmpeg()
    src_ffprobe = find_ffprobe()
    shutil.copy2(src_ffmpeg, ffmpeg_exe)
    shutil.copy2(src_ffprobe, ffprobe_exe)
    for dll in src_ffmpeg.parent.glob("*.dll"):
        shutil.copy2(dll, bin_ffmpeg / dll.name)
    print(f"Copied local FFmpeg from {src_ffmpeg.parent} -> {bin_ffmpeg}")


def _collect_heif_dlls() -> list[Path]:
    import _pillow_heif

    sp_dir = Path(_pillow_heif.__file__).resolve().parent
    patterns = (
        "lib*heif*.dll",
        "libde265*.dll",
        "libx265*.dll",
        "libgcc_s*.dll",
        "libstdc++*.dll",
        "libwinpthread*.dll",
    )
    found: list[Path] = []
    seen: set[Path] = set()
    for pat in patterns:
        for dll in sp_dir.glob(pat):
            if dll.is_file() and dll not in seen:
                seen.add(dll)
                found.append(dll)
    return found


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    bin_rife = ROOT_DIR / "bin" / "rife"
    bin_ffmpeg = ROOT_DIR / "bin" / "ffmpeg"
    install_rife(bin_rife)
    _ensure_ffmpeg_bin(bin_ffmpeg)

    dist_root = ROOT_DIR / "dist"
    build_root = ROOT_DIR / "build"
    app_dist = dist_root / "ToLivePhoto"
    if app_dist.exists():
        shutil.rmtree(app_dist)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--contents-directory",
        "_internal",
        "--specpath",
        str(build_root),
        "--distpath",
        str(dist_root),
        "--workpath",
        str(build_root / "work"),
        "--name",
        "ToLivePhoto",
        "--icon",
        str(ROOT_DIR / "tolivephoto" / "assets" / "icons" / "app_icon.ico"),
        "--add-data",
        f"{ROOT_DIR / 'tolivephoto' / 'assets'};tolivephoto/assets",
        "--add-data",
        f"{bin_ffmpeg};bin/ffmpeg",
        "--add-data",
        f"{bin_rife};bin/rife",
        "--hidden-import=_pillow_heif",
        "--collect-all=pillow_heif",
    ]

    for dll in _collect_heif_dlls():
        cmd.extend(["--add-binary", f"{dll};."])

    excludes = (
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtQuick",
        "PySide6.QtQml",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.QtPdf",
        "PySide6.QtMultimedia",
        "PySide6.QtSql",
        "tkinter",
        "pytest",
    )
    for mod in excludes:
        cmd.extend(["--exclude-module", mod])

    cmd.append(str(ROOT_DIR / "run_tolivephoto.py"))

    print("Running PyInstaller...")
    subprocess.run(cmd, cwd=str(ROOT_DIR), check=True)

    exe_path = app_dist / "ToLivePhoto.exe"
    if not exe_path.is_file():
        raise FileNotFoundError(f"Expected EXE not created: {exe_path}")

    shutil.copy2(ROOT_DIR / "README.md", app_dist / "使用说明_README.md")

    # Self-test frozen executable with stripped PATH
    report_path = build_root / "selftest_report.txt"
    if report_path.exists():
        report_path.unlink()
    clean_env = os.environ.copy()
    clean_env["PATH"] = r"C:\Windows\System32;C:\Windows"
    print("Running frozen ToLivePhoto.exe --selftest with isolated PATH...")
    subprocess.run(
        [str(exe_path), "--selftest", str(report_path)],
        cwd=str(app_dist),
        env=clean_env,
        check=True,
    )
    report_text = report_path.read_text(encoding="utf-8")
    print(report_text)
    if "SELFTEST_OK" not in report_text:
        raise RuntimeError("Frozen selftest did not report SELFTEST_OK")
    if "_internal\\bin\\ffmpeg" not in report_text or "_internal\\bin\\rife" not in report_text:
        raise RuntimeError(f"Bundled binaries were not resolved from _internal/bin:\n{report_text}")

    zip_path = dist_root / f"ToLivePhoto-v{VERSION}-Windows-x64.zip"
    if zip_path.exists():
        zip_path.unlink()
    print(f"Creating {zip_path} ...")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file_path in sorted(app_dist.rglob("*")):
            if file_path.is_file():
                arcname = Path("ToLivePhoto") / file_path.relative_to(app_dist)
                zf.write(file_path, arcname.as_posix())

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    digest = _sha256_file(zip_path)
    print(f"RELEASE_ZIP={zip_path}")
    print(f"RELEASE_SIZE_MB={size_mb:.2f}")
    print(f"RELEASE_SHA256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
