"""Resource vs. writable-data locations, frozen-aware.

Dev mode (not frozen) resolves to the repo root so existing `./results` and
`./scenarios` behavior is unchanged. Frozen builds write to a per-user
folder because the .exe may live somewhere a normal user cannot write.
There is no resource_path() — this project has no runtime-loaded binary
assets (see docs/16_Unified_Launcher_and_Packaging_Design_Document.md §5).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_root() -> Path:
    """Directory the .exe lives in (frozen) or the repo root (dev)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _platform_data_root() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata)
    return Path.home() / ".local" / "share"  # non-Windows fallback


def user_data_dir(subdir: str) -> Path:
    """Writable location for anything WraithNet creates at runtime.

    Dev mode: app_root() / subdir -- identical to today's './results',
    './scenarios' relative paths, unchanged. Frozen: a per-user writable
    folder under %LOCALAPPDATA%, because the .exe may be installed
    somewhere (e.g. Program Files) a standard user cannot write into.
    """
    base = app_root() if not is_frozen() else _platform_data_root() / "WraithNet"
    path = base / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path
