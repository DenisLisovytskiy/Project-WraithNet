"""Layer-1 style checks for shared tactical UI chrome helpers."""

from __future__ import annotations

import pytest

pytest.importorskip("panda3d")

from panda3d.core import loadPrcFileData

loadPrcFileData("", "window-type none")
loadPrcFileData("", "audio-library-name null")
loadPrcFileData("", "notify-level error")

from direct.showbase.ShowBase import ShowBase

from viewer3d.ui_chrome import (
    BTN_PRIMARY,
    make_button,
    make_entry,
    make_header,
    make_panel,
)


@pytest.fixture(scope="module")
def base():
    app = ShowBase(windowType="none")
    yield app
    app.destroy()


def test_make_panel_has_border_and_fill(base):
    panel = make_panel(base.aspect2d, frame_size=(0.0, 0.5, -0.4, 0.0))
    assert panel is not None
    assert hasattr(panel, "_chrome_fill")
    fill = panel._chrome_fill
    assert fill["frameColor"][3] > 0.5  # glass alpha


def test_make_button_primary_style(base):
    clicked = []
    btn = make_button(
        base.aspect2d,
        "Launch",
        (0.0, 0, 0.0),
        lambda: clicked.append(1),
        style="primary",
    )
    colors = btn["frameColor"]
    assert colors[0] == BTN_PRIMARY
    assert btn["text"] == "Launch"
    btn["command"]()
    assert clicked == [1]


def test_make_entry_is_dark_themed(base):
    entry = make_entry(base.aspect2d, pos=(0.0, 0, 0.0), width=8, initial="seed")
    assert entry.get() == "seed"
    bg = entry["frameColor"][0]
    assert bg[0] < 0.2 and bg[1] < 0.2 and bg[2] < 0.2


def test_make_header_uses_bracket_label(base):
    label = make_header(base.aspect2d, "[ TEST ]", pos=(0.0, 0, 0.0))
    assert label["text"] == "[ TEST ]"
