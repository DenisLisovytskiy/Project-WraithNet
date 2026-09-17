"""Pygame texture loader: checkerboard JPEGs key without raising."""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from pathlib import Path

import pytest

from viewer import textures as texmod


@pytest.fixture(autouse=True)
def _clean():
    texmod.clear_cache()
    yield
    texmod.clear_cache()


def test_missing_texture_path_is_none():
    assert texmod.texture_path("no-such-file.jpeg") is None


def test_missing_directory_degrades(monkeypatch):
    monkeypatch.setattr(
        texmod, "_candidate_dirs", lambda: [Path("no-such-dir") / "assets" / "textures"]
    )
    assert texmod.load_sprite("STRIKE_FIGHTER") is None
    assert texmod.textures_available() is False


def test_pygame_sprite_corners_are_transparent():
    pytest.importorskip("pygame")
    import pygame

    pygame.init()
    try:
        pygame.display.init()
        if not texmod.textures_available():
            pytest.skip("assets/textures not present")
        spr = texmod.load_sprite("HARDENED_BUNKER")
        assert spr is not None
        w, h = spr.get_size()
        assert spr.get_at((0, 0)).a < 30
        assert spr.get_at((w - 1, h - 1)).a < 30
        mid = spr.get_at((w // 2, h // 2))
        assert mid.a > 200
    finally:
        pygame.quit()
