# WraithNet PyInstaller spec (Phase 29)
#
# Onedir vs onefile
# -----------------
# This spec is **onedir** (`COLLECT`, equivalent to `pyinstaller -D`).
# `viewer3d/launch.py` re-invokes this same executable with `--headless` for
# every Launch click. A onefile build would unpack into a fresh temp dir for
# the GUI process *and again* for every spawned worker — extra cold-start
# latency on the exact path the editor uses. Onedir unpacks once; the GUI
# and its children share the same `_internal/` tree. Measured in this
# environment (see packaging/BUILDING.md): onedir GUI cold start vs. a
# one-shot onefile build, if both were produced.
#
# Console vs windowed
# -------------------
# `console=True` so `WraithNet.exe --headless` still prints the sim summary
# when launched from a terminal. The GUI's spawned workers already pass
# CREATE_NO_WINDOW, so Launch does not flash extra consoles. A `--windowed`
# (console=False) variant would need a run.log for frozen --headless; that
# is noted as a polish option in BUILDING.md, not this first verified recipe.
#
# Panda3D extras
# --------------
# collect_all("panda3d") / collect_all("direct") pull ShowBase, DirectGUI,
# and the wheel's DLLs. panda3d/etc (Config.prc, Confauto.prc) is *also*
# added explicitly — omitting it is the documented "No graphics pipe is
# available!" failure. libpandagl.dll / libp3windisplay.dll: whether
# collect_all already includes them is confirmed empirically in BUILDING.md
# for panda3d 1.10.16 on this machine; the spec still adds them explicitly
# so a future collect_all regression cannot silently drop the GL pipe.
#
# pygame / viewer/ are excluded. --replay stays a from-source path.

from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.building.api import COLLECT, EXE, PYZ
from PyInstaller.building.build_main import Analysis
from PyInstaller.utils.hooks import collect_all

SPECDIR = Path(SPECPATH).resolve()
REPO_ROOT = SPECDIR.parent
ENTRY = str(REPO_ROOT / "launcher" / "__main__.py")


def _merge_collect(package: str, datas, binaries, hiddenimports) -> None:
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden


datas: list = []
binaries: list = []
hiddenimports: list = []

# Only collect_all Panda3D and Direct. collect_all("mesa") walks
# mesa.examples and pulls Solara/Altair/Jupyter — none of which this
# project imports. Analysis of launcher/__main__.py already traces
# wraithnet → mesa / pydantic / pandas.
for _pkg in ("panda3d", "direct"):
    _merge_collect(_pkg, datas, binaries, hiddenimports)

hiddenimports = [
    h
    for h in hiddenimports
    if "tk" not in h.lower() and ".wx" not in h.lower() and "wxwidgets" not in h.lower()
]

import panda3d  # noqa: E402  — locate the installed wheel, not a bundled copy

_PANDA_DIR = Path(panda3d.__file__).resolve().parent
_ETC = _PANDA_DIR / "etc"
if _ETC.is_dir():
    datas.append((str(_ETC), os.path.join("panda3d", "etc")))

# Destination "." in PyInstaller 6 onedir lands in `_internal/` (not next
# to WraithNet.exe). collect_all("panda3d") already copies these DLLs into
# `_internal/panda3d/` on panda3d 1.10.16 — confirmed in the Phase 29
# verification pass. The extra copies here are cheap insurance against a
# future collect_all regression dropping the GL pipe.
for _dll_name in ("libpandagl.dll", "libp3windisplay.dll"):
    _dll = _PANDA_DIR / _dll_name
    if _dll.is_file():
        binaries.append((str(_dll), "."))
        binaries.append((str(_dll), "panda3d"))

hiddenimports += [
    "launcher",
    "launcher.cli",
    "launcher.dispatch",
    "wraithnet",
    "wraithnet.main",
    "wraithnet.paths",
    "viewer3d",
    "viewer3d.app",
    "viewer3d.argv",
    "viewer3d.launch",
]

a = Analysis(
    [ENTRY],
    pathex=[str(REPO_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pygame",
        "tkinter",
        "matplotlib",
        "IPython",
        "mesa.examples",
        "solara",
        "altair",
        "jupyter",
        "notebook",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WraithNet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="WraithNet",
)
