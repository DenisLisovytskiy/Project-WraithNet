"""Layer-1 checks for the pygame viewer's bundled-font loader.

Path resolution does not import pygame. Face loading is skipped when pygame
is not installed (requirements-dev.txt). A missing .ttf must never raise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from viewer import fonts


@pytest.fixture(autouse=True)
def _clean_cache():
    fonts.clear_cache()
    yield
    fonts.clear_cache()


def test_missing_font_file_returns_none_path():
    assert fonts.font_path("ThisFontDoesNotExist-Regular.ttf") is None


def test_missing_asset_directory_degrades(monkeypatch):
    monkeypatch.setattr(
        fonts, "_candidate_dirs", lambda: [Path("no-such-dir") / "assets" / "fonts"]
    )
    assert fonts.font_dir() is None
    assert fonts.font_path(fonts.FONT_UI) is None
    assert fonts.fonts_available() is False


def test_repo_checkout_ships_the_three_faces():
    directory = fonts.font_dir()
    if directory is None:
        pytest.skip("assets/fonts not present in this tree")
    for name in (fonts.FONT_UI, fonts.FONT_UI_REGULAR, fonts.FONT_MONO):
        assert fonts.font_path(name) is not None, name
    assert (directory / "LICENSE-SairaCondensed-OFL.txt").is_file()
    assert (directory / "LICENSE-JetBrainsMono-OFL.txt").is_file()


def test_pygame_faces_always_return_a_font():
    pytest.importorskip("pygame")
    import pygame

    pygame.font.init()
    try:
        ui = fonts.ui_font(18)
        regular = fonts.ui_font_regular(16)
        mono = fonts.mono_font(16)
        assert ui.get_height() > 0
        assert regular.get_height() > 0
        assert mono.get_height() > 0
        assert fonts.ui_font(18) is ui
    finally:
        pygame.font.quit()


def test_unloadable_face_falls_back_without_raising(monkeypatch):
    pytest.importorskip("pygame")
    import pygame

    pygame.font.init()
    try:
        monkeypatch.setattr(fonts, "font_path", lambda _name: None)
        font = fonts.load_font("missing.ttf", 18)
        assert font.get_height() > 0
    finally:
        pygame.font.quit()
