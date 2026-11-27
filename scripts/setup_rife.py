"""CLI wrapper for selective RIFE Vulkan binary/model installation."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tolivephoto.core.rife_setup import install_rife

if __name__ == "__main__":
    raise SystemExit(install_rife(ROOT_DIR / "bin" / "rife"))
