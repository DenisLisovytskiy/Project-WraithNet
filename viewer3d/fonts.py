"""Bundled UI typeface loading for viewer3d, frozen-aware and crash-proof.

The chrome (`viewer3d.ui_chrome`) asks this module for a Panda3D font and gets
either a loaded `TextFont` or `None`. `None` is a legitimate value everywhere
DirectGUI/`OnscreenText` accept a font, so a missing or corrupt .ttf degrades
to Panda3D's built-in face instead of taking the viewer down.

Faces (shipped in `assets/fonts/`, OFL — licenses ship alongside them):

* `SairaCondensed-SemiBold` — primary UI face: headers, buttons, labels.
* `SairaCondensed-Regular`  — secondary/body text.
* `JetBrainsMono-Variable`  — monospace: numeric readouts, aligned columns.

Frozen builds: PyInstaller 6 onedir puts spec `datas` with destination `"."`
inside `_internal/`, which is also what `sys._MEIPASS` points at -- but a
future spec tweak could place them next to the .exe instead, so both
candidates are probed (see `packaging/wraithnet.spec`). Dev mode resolves to
the repo root, matching `wraithnet.paths.app_root()`'s convention.
"""

from __future__ import annotations

import sys
from pathlib import Path

from panda3d.core import Filename, FontPool, SamplerState, TextFont

# Filenames, so call sites never spell out a path.
FONT_UI = "SairaCondensed-SemiBold.ttf"
FONT_UI_REGULAR = "SairaCondensed-Regular.ttf"
FONT_MONO = "JetBrainsMono-Variable.ttf"

ASSET_SUBDIR = Path("assets") / "fonts"

# 64 renders crisply at the chrome's type scale (0.024-0.046 aspect2d units)
# without blowing up the glyph atlas; Panda3D's default 40 visibly softens
# the condensed face.
PIXELS_PER_UNIT = 64.0

_cache: dict[str, TextFont | None] = {}


def _candidate_dirs() -> list[Path]:
    """Every place `assets/fonts` could live, most likely first."""
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(Path(meipass))
            dirs.append(Path(meipass).parent)  # onedir: next to the .exe
        dirs.append(Path(sys.executable).resolve().parent)
    else:
        dirs.append(Path(__file__).resolve().parent.parent)  # repo root
    return [d / ASSET_SUBDIR for d in dirs]


def font_dir() -> Path | None:
    """First existing `assets/fonts` directory, or `None` if none is present."""
    for candidate in _candidate_dirs():
        try:
            if candidate.is_dir():
                return candidate
        except OSError:  # pragma: no cover - unreadable mount
            continue
    return None


def font_path(name: str) -> Path | None:
    """Absolute path of a bundled font file, or `None` if it is not there."""
    for candidate in _candidate_dirs():
        path = candidate / name
        try:
            if path.is_file():
                return path
        except OSError:  # pragma: no cover - unreadable mount
            continue
    return None


def _tune(font: TextFont, name: str) -> None:
    """Crisper glyphs. Applied per-font so no global PRC state is touched.

    `viewer3d/app.py` owns this process's PRC config; setting
    `text-minfilter`/`text-magfilter` there would fight it, and these are
    per-`DynamicTextFont` properties anyway. The name is set because
    `FontPool` leaves it empty, which makes every font print identically.
    """
    for setter, value in (
        ("set_name", name),
        ("set_pixels_per_unit", PIXELS_PER_UNIT),
        ("set_minfilter", SamplerState.FT_linear),
        ("set_magfilter", SamplerState.FT_linear),
    ):
        try:
            getattr(font, setter)(value)
        except Exception:  # noqa: BLE001 - static font, or an older Panda3D
            pass


def load_font(name: str) -> TextFont | None:
    """Load a bundled font by filename. Cached; never raises.

    Returns `None` for a missing/unloadable file, which every DirectGUI and
    `OnscreenText` font option accepts as "use the built-in face".
    """
    if name in _cache:
        return _cache[name]
    font: TextFont | None = None
    try:
        path = font_path(name)
        if path is not None:
            # FontPool/loadFont wants a *string* panda-style path (forward
            # slashes), not a Filename object, or it silently fails on Windows.
            panda_path = Filename.from_os_specific(str(path)).get_fullpath()
            font = FontPool.load_font(panda_path)
            if font is not None and not font.is_valid():
                font = None
            if font is not None:
                _tune(font, name)
    except Exception:  # noqa: BLE001 - a font is never worth a crash
        font = None
    _cache[name] = font
    return font


def ui_font() -> TextFont | None:
    """Primary UI face (condensed semi-bold): headers, buttons, labels."""
    return load_font(FONT_UI)


def ui_font_regular() -> TextFont | None:
    """Secondary/body face (condensed regular)."""
    return load_font(FONT_UI_REGULAR)


def mono_font() -> TextFont | None:
    """Monospace face: numeric readouts and aligned table columns."""
    return load_font(FONT_MONO)


def fonts_available() -> bool:
    """True when the bundled faces actually loaded (diagnostics/tests)."""
    return ui_font() is not None


def clear_cache() -> None:
    """Drop the module-level cache. For tests; not used at runtime."""
    _cache.clear()
