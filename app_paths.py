"""Per-user application locations, independent of the source/install directory."""
from __future__ import annotations
import os
import sys
from pathlib import Path

PRODUCT_NAME = "UHV Sentinel"
APP_SLUG = "uhv-sentinel"


def user_base_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def install_dir() -> Path:
    return user_base_dir() / APP_SLUG / "installation"


def data_dir() -> Path:
    return user_base_dir() / APP_SLUG / "data"


def config_path() -> Path:
    return data_dir() / "config.json"


def runtime_python(root: Path | None = None, *, gui: bool = False) -> Path:
    root = root or install_dir()
    if sys.platform == "win32":
        return root / "runtime" / "Scripts" / ("pythonw.exe" if gui else "python.exe")
    return root / "runtime" / "bin" / "python"
