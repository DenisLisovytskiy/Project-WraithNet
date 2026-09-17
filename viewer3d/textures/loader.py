"""Bundled unit/terrain texture loading for viewer3d.

JPEGs from Gemini do not carry alpha — they paint a checkerboard behind the
subject. This loader keys that checker (plus the corner sparkle) to
transparency, then uploads a Panda3D Texture. A missing file returns None
and the scene falls back to the procedural glyphs.

Frozen-aware path probing matches `viewer3d.fonts`.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

from panda3d.core import (
    Filename,
    PNMImage,
    SamplerState,
    Texture,
)

ASSET_SUBDIR = Path("assets") / "textures"
SPRITE_PX = 512
TERRAIN_PX = 512

# Relative to ASSET_SUBDIR.
SPRITE_FILES: dict[str, str] = {
    "INTERCEPTOR": "interceptor.jpeg",
    "STRIKE_FIGHTER": "strike_fighter.jpeg",
    "HEAVY_BOMBER": "heavy_bomber.jpeg",
    "HARDENED_BUNKER": "hardened_bunker.jpeg",
    "MOBILE_RADAR": "mobile_radar.jpeg",
    "SAM_SITE": "sam_site.jpeg",
    "AAA_SITE": "aaa_light.jpeg",
    "AAA_LIGHT": "aaa_light.jpeg",
    "AAA_TURRETED": "aaa_turreted.jpeg",
    "JAMMER": "ew_jammer.jpeg",
    "HOME_BASE": "home_base.jpeg",
    "MBT_LIGHT_FRIENDLY": "mbt_light_friendly.jpeg",
    "MBT_LIGHT_ENEMY": "mbt_light_enemy.jpeg",
    "MBT_STANDARD_FRIENDLY": "mbt_standard_friendly.jpeg",
    "MBT_STANDARD_ENEMY": "mbt_standard_enemy.jpeg",
    "ESCORT_CARRIER": "escort_carrier.jpeg",
    "FLEET_CARRIER": "fleet_carrier.jpeg",
    "CORVETTE_LIGHT": "corvette_light.jpeg",
    "CORVETTE_STANDARD": "corvette_standard.jpeg",
}

TERRAIN_FILES: dict[str, str] = {
    "WATER": "terrains/terrain_water.jpeg",
    "PLAINS": "terrains/terrain_plains.jpeg",
    "FOREST": "terrains/terrain_forest.jpeg",
    "CITY": "terrains/terrain_city.jpeg",
}

# World-unit half-extent of the billboard card (full width = 2 * half).
SPRITE_HALF: dict[str, float] = {
    "INTERCEPTOR": 1.25,
    "STRIKE_FIGHTER": 1.35,
    "HEAVY_BOMBER": 1.55,
    "HARDENED_BUNKER": 1.45,
    "MOBILE_RADAR": 1.35,
    "SAM_SITE": 1.45,
    "AAA_SITE": 1.35,
    "AAA_LIGHT": 1.35,
    "AAA_TURRETED": 1.4,
    "JAMMER": 1.35,
    "HOME_BASE": 1.85,
    "MBT_LIGHT_FRIENDLY": 1.25,
    "MBT_LIGHT_ENEMY": 1.25,
    "MBT_STANDARD_FRIENDLY": 1.4,
    "MBT_STANDARD_ENEMY": 1.4,
    "ESCORT_CARRIER": 2.2,
    "FLEET_CARRIER": 2.7,
    "CORVETTE_LIGHT": 1.55,
    "CORVETTE_STANDARD": 1.75,
}

_cache: dict[tuple[str, str], Texture | None] = {}


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(Path(meipass))
            dirs.append(Path(meipass).parent)
        dirs.append(Path(sys.executable).resolve().parent)
    else:
        dirs.append(Path(__file__).resolve().parent.parent.parent)
    return [d / ASSET_SUBDIR for d in dirs]


def texture_dir() -> Path | None:
    for candidate in _candidate_dirs():
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            continue
    return None


def texture_path(rel: str) -> Path | None:
    for candidate in _candidate_dirs():
        path = candidate / rel
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def tank_sprite_key(frame_type: str, faction: str) -> str:
    kind = "LIGHT" if "LIGHT" in (frame_type or "").upper() else "STANDARD"
    side = "FRIENDLY" if str(faction).upper() == "FRIENDLY" else "ENEMY"
    return f"MBT_{kind}_{side}"


def sprite_half(key: str) -> float:
    return SPRITE_HALF.get(key, 1.35)


def _read_pnm(path: Path) -> PNMImage | None:
    img = PNMImage()
    panda_path = Filename.from_os_specific(str(path)).get_fullpath()
    if not img.read(panda_path):
        return None
    return img


def _resize(src: PNMImage, size: int) -> PNMImage:
    if src.get_x_size() == size and src.get_y_size() == size:
        return src
    dst = PNMImage(size, size)
    dst.quick_filter_from(src)
    return dst


def _rgb(img: PNMImage, x: int, y: int) -> tuple[float, float, float]:
    return (img.get_red(x, y), img.get_green(x, y), img.get_blue(x, y))


def _dist2(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def _sample_checker(img: PNMImage) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Two gray clusters from the four corners (JPEG-blurred checker)."""
    w, h = img.get_x_size(), img.get_y_size()
    patch = min(32, w, h)
    samples: list[tuple[float, float, float]] = []
    for ox, oy in ((0, 0), (w - patch, 0), (0, h - patch), (w - patch, h - patch)):
        for y in range(oy, oy + patch, 2):
            for x in range(ox, ox + patch, 2):
                r, g, b = _rgb(img, x, y)
                if max(r, g, b) - min(r, g, b) < 0.07:
                    samples.append((r, g, b))
    if not samples:
        return (0.25, 0.25, 0.25), (0.4, 0.4, 0.4)
    samples.sort(key=lambda p: p[0] + p[1] + p[2])
    lo = samples[len(samples) // 10]
    hi = samples[(len(samples) * 9) // 10]
    return lo, hi


def _key_checkerboard(img: PNMImage) -> None:
    """Flood from the edges: pixels near the corner checker become alpha 0."""
    if not img.has_alpha():
        img.add_alpha()
    w, h = img.get_x_size(), img.get_y_size()
    c1, c2 = _sample_checker(img)
    thresh = (24.0 / 255.0) ** 2
    marked = bytearray(w * h)
    q: deque[tuple[int, int]] = deque()

    def consider(x: int, y: int) -> None:
        i = y * w + x
        if marked[i]:
            return
        rgb = _rgb(img, x, y)
        if _dist2(rgb, c1) <= thresh or _dist2(rgb, c2) <= thresh:
            marked[i] = 1
            q.append((x, y))

    for x in range(w):
        consider(x, 0)
        consider(x, h - 1)
    for y in range(h):
        consider(0, y)
        consider(w - 1, y)
    while q:
        x, y = q.popleft()
        if x > 0:
            consider(x - 1, y)
        if x + 1 < w:
            consider(x + 1, y)
        if y > 0:
            consider(x, y - 1)
        if y + 1 < h:
            consider(x, y + 1)

    for y in range(h):
        row = y * w
        for x in range(w):
            if marked[row + x]:
                img.set_alpha(x, y, 0.0)
            else:
                img.set_alpha(x, y, 1.0)

    # Feather: opaque pixels next to keyed ones go half-clear.
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            if marked[y * w + x]:
                continue
            if (
                marked[y * w + x - 1]
                or marked[y * w + x + 1]
                or marked[(y - 1) * w + x]
                or marked[(y + 1) * w + x]
            ):
                img.set_alpha(x, y, 0.4)

    _stamp_sparkle(img)


def _stamp_sparkle(img: PNMImage) -> None:
    """Gemini watermark lives in the bottom-right corner as a dark star."""
    w, h = img.get_x_size(), img.get_y_size()
    rw, rh = max(8, w // 12), max(8, h // 12)
    for y in range(h - rh, h):
        for x in range(w - rw, w):
            r, g, b = _rgb(img, x, y)
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            sat = max(r, g, b) - min(r, g, b)
            if lum < 0.12 and sat < 0.08:
                img.set_alpha(x, y, 0.0)


def _pnm_to_texture(
    img: PNMImage, name: str, *, repeat: bool
) -> Texture:
    tex = Texture(name)
    tex.load(img)
    wrap = SamplerState.WM_repeat if repeat else SamplerState.WM_clamp
    tex.set_wrap_u(wrap)
    tex.set_wrap_v(wrap)
    tex.set_minfilter(SamplerState.FT_linear_mipmap_linear)
    tex.set_magfilter(SamplerState.FT_linear)
    tex.set_format(Texture.F_rgba)
    return tex


def load_sprite(key: str) -> Texture | None:
    """Unit billboard, checker keyed. Cached; never raises."""
    cache_key = ("sprite", key)
    if cache_key in _cache:
        return _cache[cache_key]
    tex: Texture | None = None
    try:
        rel = SPRITE_FILES.get(key)
        path = texture_path(rel) if rel else None
        if path is not None:
            src = _read_pnm(path)
            if src is not None:
                img = _resize(src, SPRITE_PX)
                _key_checkerboard(img)
                tex = _pnm_to_texture(img, key, repeat=False)
    except Exception:  # noqa: BLE001
        tex = None
    _cache[cache_key] = tex
    return tex


def _heal_sparkle(img: PNMImage) -> None:
    """Overwrite the Gemini star on a tiling albedo by cloning an interior pixel."""
    w, h = img.get_x_size(), img.get_y_size()
    rw, rh = max(8, w // 12), max(8, h // 12)
    src = _rgb(img, max(0, w - rw - 8), max(0, h - rh - 8))
    for y in range(h - rh, h):
        for x in range(w - rw, w):
            r, g, b = _rgb(img, x, y)
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            sat = max(r, g, b) - min(r, g, b)
            if lum < 0.12 and sat < 0.08:
                img.set_xel(x, y, src[0], src[1], src[2])


def load_terrain(kind: str) -> Texture | None:
    """Seamless terrain albedo. Sparkle cloned out; no checker flood."""
    cache_key = ("terrain", kind)
    if cache_key in _cache:
        return _cache[cache_key]
    tex: Texture | None = None
    try:
        rel = TERRAIN_FILES.get(kind)
        path = texture_path(rel) if rel else None
        if path is not None:
            src = _read_pnm(path)
            if src is not None:
                img = _resize(src, TERRAIN_PX)
                if not img.has_alpha():
                    img.add_alpha()
                    img.alpha_fill(1.0)
                _heal_sparkle(img)
                tex = _pnm_to_texture(img, f"terrain-{kind}", repeat=True)
    except Exception:  # noqa: BLE001
        tex = None
    _cache[cache_key] = tex
    return tex


def textures_available() -> bool:
    return texture_path(SPRITE_FILES["STRIKE_FIGHTER"]) is not None


def clear_cache() -> None:
    _cache.clear()
