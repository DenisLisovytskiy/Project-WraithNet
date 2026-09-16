"""Layer-1 checks for the bundled-font loader.

The contract is deliberately weak on *which* font comes back (a build without
`assets/fonts/` is legal) and strong on "never raises, never blocks startup":
`None` is a valid font everywhere DirectGUI/OnscreenText accept one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("panda3d")

from panda3d.core import loadPrcFileData

loadPrcFileData("", "window-type none")
loadPrcFileData("", "audio-library-name null")
loadPrcFileData("", "notify-level error")

from viewer3d import fonts


@pytest.fixture(autouse=True)
def _clean_cache():
    fonts.clear_cache()
    yield
    fonts.clear_cache()


def _ok(font) -> bool:
    return font is None or font.is_valid()


def test_accessors_return_a_valid_font_or_none():
    assert _ok(fonts.ui_font())
    assert _ok(fonts.ui_font_regular())
    assert _ok(fonts.mono_font())


def test_missing_font_file_returns_none_without_raising():
    assert fonts.load_font("ThisFontDoesNotExist-Regular.ttf") is None
    # Negative results are cached too; a second call must stay quiet.
    assert fonts.load_font("ThisFontDoesNotExist-Regular.ttf") is None


def test_missing_asset_directory_degrades_to_none(monkeypatch):
    monkeypatch.setattr(
        fonts, "_candidate_dirs", lambda: [Path("no-such-dir") / "assets" / "fonts"]
    )
    assert fonts.font_dir() is None
    assert fonts.font_path(fonts.FONT_UI) is None
    assert fonts.load_font(fonts.FONT_UI) is None
    assert fonts.ui_font() is None
    assert fonts.fonts_available() is False


def test_fonts_are_cached_not_reloaded_per_widget():
    first = fonts.ui_font()
    assert fonts.ui_font() is first
    assert fonts.FONT_UI in fonts._cache


def test_repo_checkout_ships_the_three_faces():
    """Dev-mode resolution: the assets committed to the repo are found."""
    directory = fonts.font_dir()
    if directory is None:  # a stripped checkout is allowed, just not ours
        pytest.skip("assets/fonts not present in this tree")
    for name in (fonts.FONT_UI, fonts.FONT_UI_REGULAR, fonts.FONT_MONO):
        assert fonts.font_path(name) is not None, name
    # OFL requires the licenses travel with the fonts.
    assert (directory / "LICENSE-SairaCondensed-OFL.txt").is_file()
    assert (directory / "LICENSE-JetBrainsMono-OFL.txt").is_file()


def test_loaded_font_uses_the_tuned_pixels_per_unit():
    font = fonts.ui_font()
    if font is None:
        pytest.skip("bundled font not loadable in this environment")
    assert font.get_pixels_per_unit() == pytest.approx(fonts.PIXELS_PER_UNIT)
