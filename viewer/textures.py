"""Bundled unit/terrain texture loading for the pygame replay viewer.

Does not import Panda3D or `viewer3d`. JPEG checkerboards are keyed to
alpha the same way as the 3D loader: flood from the corners, then stamp
out the Gemini sparkle.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path
from typing import Any

ASSET_SUBDIR = Path("assets") / "textures"
SPRITE_PX = 512
TERRAIN_PX = 512

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
}

TERRAIN_FILES: dict[str, str] = {
    "WATER": "terrains/terrain_water.jpeg",
    "PLAINS": "terrains/terrain_plains.jpeg",
    "FOREST": "terrains/terrain_forest.jpeg",
    "CITY": "terrains/terrain_city.jpeg",
}

SPRITE_PX_ON_MAP: dict[str, int] = {
    "INTERCEPTOR": 40,
    "STRIKE_FIGHTER": 44,
    "HEAVY_BOMBER": 52,
    "HARDENED_BUNKER": 48,
    "MOBILE_RADAR": 44,
    "SAM_SITE": 48,
    "AAA_SITE": 44,
    "AAA_LIGHT": 44,
    "AAA_TURRETED": 46,
    "JAMMER": 44,
    "HOME_BASE": 56,
}

_cache: dict[tuple[str, str], Any] = {}


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(Path(meipass))
            dirs.append(Path(meipass).parent)
        dirs.append(Path(sys.executable).resolve().parent)
    else:
        dirs.append(Path(__file__).resolve().parent.parent)
    return [d / ASSET_SUBDIR for d in dirs]


def texture_path(rel: str) -> Path | None:
    for candidate in _candidate_dirs():
        path = candidate / rel
        try:
            if path.is_file():
                return path
        except OSError:
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


def _rgb_at(buf: bytearray, w: int, x: int, y: int) -> tuple[int, int, int]:
    i = (y * w + x) * 4
    return buf[i], buf[i + 1], buf[i + 2]


def _dist2(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def _sample_checker(buf: bytearray, w: int, h: int) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    patch = min(32, w, h)
    samples: list[tuple[int, int, int]] = []
    for ox, oy in ((0, 0), (w - patch, 0), (0, h - patch), (w - patch, h - patch)):
        for y in range(oy, oy + patch, 2):
            for x in range(ox, ox + patch, 2):
                r, g, b = _rgb_at(buf, w, x, y)
                if max(r, g, b) - min(r, g, b) < 18:
                    samples.append((r, g, b))
    if not samples:
        return (64, 64, 64), (100, 100, 100)
    samples.sort(key=lambda p: p[0] + p[1] + p[2])
    return samples[len(samples) // 10], samples[(len(samples) * 9) // 10]


def _key_checkerboard(buf: bytearray, w: int, h: int) -> None:
    c1, c2 = _sample_checker(buf, w, h)
    thresh = 24 * 24
    marked = bytearray(w * h)
    q: deque[tuple[int, int]] = deque()

    def consider(x: int, y: int) -> None:
        i = y * w + x
        if marked[i]:
            return
        rgb = _rgb_at(buf, w, x, y)
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
            a = 0 if marked[row + x] else 255
            if a and (
                (x and marked[row + x - 1])
                or (x + 1 < w and marked[row + x + 1])
                or (y and marked[row - w + x])
                or (y + 1 < h and marked[row + w + x])
            ):
                a = 100
            buf[(row + x) * 4 + 3] = a

    rw, rh = max(8, w // 12), max(8, h // 12)
    for y in range(h - rh, h):
        for x in range(w - rw, w):
            i = (y * w + x) * 4
            r, g, b = buf[i], buf[i + 1], buf[i + 2]
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            sat = max(r, g, b) - min(r, g, b)
            if lum < 30 and sat < 20:
                buf[i + 3] = 0


def _load_surface(rel: str, size: int, *, key: bool) -> Any:
    pygame = _require_pygame()
    path = texture_path(rel)
    if path is None:
        return None
    loaded = pygame.image.load(str(path))
    scaled = pygame.transform.smoothscale(loaded, (size, size))
    rgb = pygame.image.tobytes(scaled, "RGB")
    buf = bytearray(size * size * 4)
    for i in range(size * size):
        o, s = i * 4, i * 3
        buf[o] = rgb[s]
        buf[o + 1] = rgb[s + 1]
        buf[o + 2] = rgb[s + 2]
        buf[o + 3] = 255
    if key:
        _key_checkerboard(buf, size, size)
    else:
        rw, rh = max(8, size // 12), max(8, size // 12)
        j = ((size - rh - 4) * size + (size - rw - 4)) * 4
        for y in range(size - rh, size):
            for x in range(size - rw, size):
                i = (y * size + x) * 4
                r, g, b = buf[i], buf[i + 1], buf[i + 2]
                lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
                sat = max(r, g, b) - min(r, g, b)
                if lum < 30 and sat < 20:
                    buf[i : i + 3] = buf[j : j + 3]
    return pygame.image.frombytes(bytes(buf), (size, size), "RGBA")


def load_sprite(key: str):
    cache_key = ("sprite", key)
    if cache_key in _cache:
        return _cache[cache_key]
    surf = None
    try:
        rel = SPRITE_FILES.get(key)
        if rel:
            surf = _load_surface(rel, SPRITE_PX, key=True)
    except Exception:  # noqa: BLE001
        surf = None
    _cache[cache_key] = surf
    return surf


def load_terrain(kind: str):
    cache_key = ("terrain", kind)
    if cache_key in _cache:
        return _cache[cache_key]
    surf = None
    try:
        rel = TERRAIN_FILES.get(kind)
        if rel:
            surf = _load_surface(rel, TERRAIN_PX, key=False)
    except Exception:  # noqa: BLE001
        surf = None
    _cache[cache_key] = surf
    return surf


def sprite_map_size(key: str) -> int:
    return SPRITE_PX_ON_MAP.get(key, 44)


def textures_available() -> bool:
    return texture_path(SPRITE_FILES["STRIKE_FIGHTER"]) is not None


def clear_cache() -> None:
    _cache.clear()
