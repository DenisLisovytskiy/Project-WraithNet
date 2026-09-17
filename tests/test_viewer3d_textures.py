"""Texture loader: missing files never raise; JPEGs key to a transparent corner."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("panda3d")

from panda3d.core import loadPrcFileData

loadPrcFileData("", "window-type none")
loadPrcFileData("", "audio-library-name null")
loadPrcFileData("", "notify-level error")

from viewer3d import textures


@pytest.fixture(autouse=True)
def _clean():
    textures.clear_cache()
    yield
    textures.clear_cache()


def test_missing_texture_returns_none():
    assert textures.texture_path("no-such-file.jpeg") is None
    assert textures.load_sprite("NOT_A_UNIT") is None


def test_missing_directory_degrades(monkeypatch):
    monkeypatch.setattr(
        textures, "_candidate_dirs", lambda: [Path("no-such-dir") / "assets" / "textures"]
    )
    assert textures.texture_dir() is None
    assert textures.load_sprite("STRIKE_FIGHTER") is None
    assert textures.textures_available() is False


def test_repo_checkout_has_the_gemini_jpegs():
    if not textures.textures_available():
        pytest.skip("assets/textures not present")
    for key in ("STRIKE_FIGHTER", "HARDENED_BUNKER", "SAM_SITE", "AAA_LIGHT"):
        assert textures.load_sprite(key) is not None, key
    assert textures.load_terrain("PLAINS") is not None


def test_keyed_sprite_has_a_transparent_corner():
    tex = textures.load_sprite("STRIKE_FIGHTER")
    if tex is None:
        pytest.skip("strike fighter jpeg not present")
    from panda3d.core import PNMImage

    img = PNMImage()
    assert tex.store(img)
    assert img.has_alpha()
    # Corners were the Gemini checker and must not be opaque.
    assert img.get_alpha(0, 0) < 0.1
    assert img.get_alpha(img.get_x_size() - 1, img.get_y_size() - 1) < 0.1
    # The aircraft itself should keep some opaque pixels.
    opaque = 0
    step = max(1, img.get_x_size() // 32)
    for y in range(0, img.get_y_size(), step):
        for x in range(0, img.get_x_size(), step):
            if img.get_alpha(x, y) > 0.8:
                opaque += 1
    assert opaque > 10
