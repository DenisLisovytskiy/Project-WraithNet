"""Bundled UI typeface loading for the pygame replay viewer.

Mirrors `viewer3d.fonts` path resolution so both surfaces share the same
OFL faces under `assets/fonts/`, but this module never imports Panda3D
(or `viewer3d`). A missing or unloadable .ttf degrades to pygame's built-in
face — `pygame.font.Font` always returns a Font, so call sites never have
to special-case `None` at blit time.

Faces (shipped in `assets/fonts/`, OFL — licenses ship alongside them):

* `SairaCondensed-SemiBold` — primary UI face: headers, titles.
* `SairaCondensed-Regular`  — secondary/body text.
* `JetBrainsMono-Variable`  — monospace: numeric readouts, aligned columns.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Filenames, so call sites never spell out a path.
FONT_UI = "SairaCondensed-SemiBold.ttf"
FONT_UI_REGULAR = "SairaCondensed-Regular.ttf"
FONT_MONO = "JetBrainsMono-Variable.ttf"

ASSET_SUBDIR = Path("assets") / "fonts"

_cache: dict[tuple[str, int], Any] = {}


def _candidate_dirs() -> list[Path]:
    """Every place `assets/fonts` could live, most likely first."""
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(Path(meipass))
            dirs.append(Path(meipass).parent)
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


def _require_pygame():
    try:
        import pygame
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "pygame is required for rendering; pip install -r requirements-viz.txt"
        ) from exc
    return pygame


def load_font(name: str, size: int):
    """Load a bundled face at `size` px. Cached; never raises.

    Always returns a `pygame.font.Font`. A missing file, a corrupt TTF, or
    a pygame that cannot open the face all degrade to `Font(None, size)`.
    Cached faces are dropped if the font module was quit (tests call
    `pygame.quit()` between cases).
    """
    pygame = _require_pygame()
    if not pygame.font.get_init():
        pygame.font.init()
        _cache.clear()
    key = (name, int(size))
    cached = _cache.get(key)
    if cached is not None:
        try:
            cached.get_height()
            return cached
        except pygame.error:
            _cache.pop(key, None)
    font = None
    try:
        path = font_path(name)
        if path is not None:
            font = pygame.font.Font(str(path), int(size))
    except Exception:  # noqa: BLE001 - a font is never worth a crash
        font = None
    if font is None:
        font = pygame.font.Font(None, int(size))
    _cache[key] = font
    return font


def ui_font(size: int):
    """Primary UI face (condensed semi-bold): headers, titles."""
    return load_font(FONT_UI, size)


def ui_font_regular(size: int):
    """Secondary/body face (condensed regular)."""
    return load_font(FONT_UI_REGULAR, size)


def mono_font(size: int):
    """Monospace face: numeric readouts and aligned columns."""
    return load_font(FONT_MONO, size)


def fonts_available() -> bool:
    """True when the bundled UI face file is on disk (diagnostics/tests)."""
    return font_path(FONT_UI) is not None


def clear_cache() -> None:
    """Drop the module-level cache. For tests; not used at runtime."""
    _cache.clear()
