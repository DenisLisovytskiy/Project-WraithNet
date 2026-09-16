"""Layer-1 style checks for shared tactical UI chrome helpers."""

from __future__ import annotations

import pytest

pytest.importorskip("panda3d")

from panda3d.core import loadPrcFileData

loadPrcFileData("", "window-type none")
loadPrcFileData("", "audio-library-name null")
loadPrcFileData("", "notify-level error")

from direct.gui import DirectGuiGlobals as DGG
from direct.showbase.ShowBase import ShowBase

from panda3d.core import TextNode

from viewer3d.ui_chrome import (
    BTN_PRIMARY,
    make_button,
    make_entry,
    make_header,
    make_panel,
)
from viewer3d import ui_chrome as uc


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


def test_make_entry_keeps_a_long_path_inside_the_well(base):
    """Overflow + scissor: the string stays intact, the plate stays finite."""
    long_path = r"C:\PERSONAL\Projekty\Project-WraithNet\scenarios\custom.json"
    entry = make_entry(
        base.aspect2d,
        pos=(0.0, 0, 0.0),
        width=8,
        scale=uc.TYPE_MICRO,
        initial=long_path,
        overflow=True,
    )
    assert entry.get() == long_path
    assert entry["overflow"]
    well = entry.guiItem.get_frame()
    assert float(well[1]) > float(well[0])
    # Character-width box for width=8, plus sunken pad/border slack.
    assert (float(well[1]) - float(well[0])) < 12.0
    assert entry.hasScissor()


def test_make_header_uses_bracket_label(base):
    label = make_header(base.aspect2d, "[ TEST ]", pos=(0.0, 0, 0.0))
    assert label["text"] == "[ TEST ]"


# --- design tokens -------------------------------------------------------


def test_type_scale_respects_the_legibility_floor():
    assert min(uc.TYPE_SCALE) >= uc.LEGIBILITY_FLOOR
    assert uc.TYPE_MIN == min(uc.TYPE_SCALE)
    assert uc.LEGIBILITY_FLOOR >= 0.024
    # Five distinct, strictly descending steps.
    assert len(uc.TYPE_SCALE) == 5
    assert list(uc.TYPE_SCALE) == sorted(uc.TYPE_SCALE, reverse=True)
    assert len(set(uc.TYPE_SCALE)) == 5


def test_clamp_scale_lifts_illegible_sizes():
    assert uc.clamp_scale(0.018) == uc.LEGIBILITY_FLOOR
    assert uc.clamp_scale(uc.TYPE_BODY) == uc.TYPE_BODY


def test_spacing_scale_is_a_rhythm():
    assert list(uc.SPACE_SCALE) == sorted(uc.SPACE_SCALE)
    assert len(set(uc.SPACE_SCALE)) == len(uc.SPACE_SCALE)
    assert uc.SPACE_1 > 0
    assert uc.ROW_H >= uc.TYPE_BODY
    assert 0 < uc.ROW_H_TIGHT < uc.ROW_H


def test_row_z_and_stack_agree_and_never_collide():
    assert uc.row_z(0, top=-0.1) == pytest.approx(-0.1)
    assert uc.row_z(3, top=0.0) == pytest.approx(-3 * uc.ROW_H)

    stack = uc.Stack(-0.10, x=0.08)
    first = stack.row()
    second = stack.row()
    assert first == pytest.approx(-0.10)
    assert second < first
    assert first - second == pytest.approx(uc.ROW_H)
    stack.gap(uc.SPACE_2)
    x, y, z = stack.pos()
    assert (x, y) == (pytest.approx(0.08), pytest.approx(0.0))
    assert z < second
    assert stack.height() > 0


def test_palette_gives_buttons_contrast_against_the_panel():
    def luma(color):
        return 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]

    # A resting button must be clearly lighter than the panel it sits on,
    # which is what the old near-black BTN failed to do.
    assert luma(uc.BTN) - luma(uc.BG_PANEL) > 0.08
    # Hover / press / disabled must all be tellable apart from rest.
    for other in (uc.BTN_HOVER, uc.BTN_PRESS, uc.BTN_DISABLED):
        assert abs(luma(uc.BTN) - luma(other)) > 0.02
    assert luma(uc.BTN_HOVER) > luma(uc.BTN) > luma(uc.BTN_PRESS)
    # Disabled is desaturated, not merely dark-and-transparent.
    assert uc.BTN_DISABLED[3] >= 0.9
    assert max(uc.BTN_DISABLED[:3]) - min(uc.BTN_DISABLED[:3]) < 0.1
    # Body text stays comfortably legible on the panel fill.
    assert luma(uc.TEXT) - luma(uc.BG_PANEL) > 0.5
    assert luma(uc.TEXT_MUTED) - luma(uc.BG_PANEL) > 0.3


def test_panel_fill_stays_opaque_enough_to_read_over_the_scene():
    assert uc.BG_PANEL[3] > 0.5
    assert uc.BG_NESTED[3] > 0.5
    assert uc.BG_DEEP[3] > 0.5


def test_legacy_export_surface_is_intact():
    required = [
        "BG_PANEL",
        "BG_NESTED",
        "BG_DEEP",
        "BORDER",
        "BORDER_DIM",
        "CYAN",
        "CYAN_DIM",
        "TEXT",
        "TEXT_MUTED",
        "WHITE",
        "BTN",
        "BTN_HOVER",
        "BTN_PRESS",
        "BTN_DISABLED",
        "BTN_PRIMARY",
        "BTN_PRIMARY_HOVER",
        "BTN_PRIMARY_PRESS",
        "BTN_DANGER",
        "BTN_DANGER_HOVER",
        "BTN_DANGER_PRESS",
        "ENTRY_BG",
        "ENTRY_FOCUS",
        "ENTRY_BORDER",
        "GREEN",
        "RED",
        "AMBER",
        "PANEL_BG",
        "BTN_BG",
    ]
    for name in required:
        color = getattr(uc, name)
        assert len(color) == 4, name
        assert all(0.0 <= channel <= 1.0 for channel in color), name
    for name in (
        "make_panel",
        "make_header",
        "make_label",
        "make_button",
        "make_entry",
        "style_existing_entry",
    ):
        assert callable(getattr(uc, name)), name


def test_semantic_status_roles_are_distinct():
    roles = {
        uc.STATUS_FRIENDLY,
        uc.STATUS_HOSTILE,
        uc.STATUS_WARNING,
        uc.STATUS_NEUTRAL,
        uc.STATUS_INACTIVE,
    }
    assert len(roles) == 5
    for tone in ("neutral", "friendly", "hostile", "warning", "inactive"):
        assert len(uc.tone_color(tone)) == 4


# --- relief: the bug class that made the HUD invisible -------------------


def _text_color(widget, state: int = 0) -> tuple[float, float, float, float]:
    """Read a widget's actual glyph color.

    `widget["text_fg"]` is write-only in DirectGUI: `cget` forwards to
    `OnscreenText.getFg`, which does not exist (the getter is name-mangled).
    """
    return tuple(widget.component(f"text{state}").textNode.get_text_color())


def _has_visible_fill(widget) -> bool:
    color = widget["frameColor"]
    if color is None:
        return False
    first = color[0]
    if hasattr(first, "__len__"):  # 4-state color
        return any(state[3] > 0.0 for state in color)
    return color[3] > 0.0


def _assert_paints(widget, label: str) -> None:
    assert _has_visible_fill(widget), f"{label}: expected a visible fill"
    # relief=None makes DirectGUI ignore frameColor entirely.
    assert widget["relief"] is not None, f"{label}: relief=None paints nothing"


def test_no_filled_chrome_widget_is_built_with_relief_none(base):
    panel = make_panel(base.aspect2d, frame_size=(0.0, 0.6, -0.5, 0.0), title="[ P ]")
    _assert_paints(panel, "panel border")
    _assert_paints(panel._chrome_fill, "panel fill")
    _assert_paints(panel._chrome_rule, "panel title rule")

    nested = make_panel(
        panel,
        frame_size=uc.nest_frame_size((0.0, 0.6, -0.5, 0.0)),
        nested=True,
    )
    _assert_paints(nested, "nested panel border")
    _assert_paints(nested._chrome_fill, "nested panel fill")

    _assert_paints(uc.make_rule(panel, pos=(0.05, 0, -0.2), width=0.4), "rule")
    _assert_paints(
        uc.make_separator(panel, pos=(0.05, 0, -0.25), width=0.4), "separator"
    )
    _assert_paints(uc.make_badge(panel, "LIVE", pos=(0.05, 0, -0.3)), "badge")
    row = uc.make_kv_row(panel, "ALT", "100", pos=(0.05, 0, -0.35), background=True)
    _assert_paints(row.background, "kv-row background")
    _assert_paints(uc.make_button(panel, "GO", (0.3, 0, -0.4)), "button")
    _assert_paints(uc.make_entry(panel, pos=(0.3, 0, -0.45), width=4), "entry")

    section = uc.make_section(panel, "[ S ]", pos=(0.05, 0, -0.42), width=0.4)
    _assert_paints(section.rule, "section rule")

    grid = uc.make_field_grid(panel, ["x", "y"], pos=(0.05, 0, -0.46))
    for key, entry in grid.entries.items():
        _assert_paints(entry, f"grid entry {key}")


def test_relief_none_from_a_caller_falls_back_to_a_painting_relief(base):
    panel = make_panel(
        base.aspect2d, frame_size=(0.0, 0.4, -0.3, 0.0), relief=None
    )
    assert panel["relief"] is not None
    btn = make_button(base.aspect2d, "X", (0.0, 0, 0.0), relief=None)
    assert btn["relief"] is not None


# --- panels --------------------------------------------------------------


def test_panel_title_is_integrated_and_leaves_room_for_content(base):
    panel = make_panel(
        base.aspect2d, frame_size=(0.0, 0.9, -0.8, 0.0), title="[ BRIEF ]"
    )
    assert panel._chrome_title["text"] == "[ BRIEF ]"
    assert panel._chrome_rule is not None
    content_top = uc.panel_content_top(panel)
    assert -0.8 < content_top < 0.0
    assert content_top < panel._chrome_title.get_z()


def test_panel_without_title_has_no_title_widgets(base):
    panel = make_panel(base.aspect2d, frame_size=(0.0, 0.5, -0.4, 0.0))
    assert panel._chrome_title is None
    assert panel._chrome_rule is None
    assert uc.panel_content_top(panel) < 0.0


def test_panel_fill_is_inset_inside_its_border(base):
    panel = make_panel(base.aspect2d, frame_size=(0.0, 0.5, -0.4, 0.0))
    left, right, bottom, top = panel["frameSize"]
    f_left, f_right, f_bottom, f_top = panel._chrome_fill["frameSize"]
    assert f_left > left and f_right < right
    assert f_bottom > bottom and f_top < top
    assert f_right > f_left and f_top > f_bottom  # non-degenerate


def test_panel_survives_a_rect_thinner_than_its_border(base):
    panel = make_panel(base.aspect2d, frame_size=(0.0, 0.004, -0.004, 0.0))
    f_left, f_right, f_bottom, f_top = panel._chrome_fill["frameSize"]
    assert f_right >= f_left and f_top >= f_bottom


def test_nest_frame_size_keeps_a_child_clear_of_the_parent_border():
    parent = (0.0, 0.9, -0.8, 0.0)
    child = uc.nest_frame_size(parent, pad=uc.SPACE_2)
    assert child[0] > parent[0] + uc.BORDER_W
    assert child[1] < parent[1] - uc.BORDER_W
    assert child[2] > parent[2] + uc.BORDER_W
    assert child[3] < parent[3] - uc.BORDER_W
    clipped = uc.nest_frame_size(parent, top=-0.3, bottom=-0.7)
    assert clipped[3] == pytest.approx(-0.3)
    assert clipped[2] == pytest.approx(-0.7)


def test_panel_inset_shrinks_the_whole_frame(base):
    panel = make_panel(
        base.aspect2d, frame_size=(0.0, 0.5, -0.4, 0.0), inset=uc.SPACE_1
    )
    left, right, bottom, top = panel._chrome_frame_size
    assert left == pytest.approx(uc.SPACE_1)
    assert right == pytest.approx(0.5 - uc.SPACE_1)
    assert bottom == pytest.approx(-0.4 + uc.SPACE_1)
    assert top == pytest.approx(-uc.SPACE_1)


# --- buttons -------------------------------------------------------------


def test_every_button_style_constructs_with_four_states(base):
    for style in uc.BUTTON_STYLES:
        btn = make_button(base.aspect2d, style.upper(), (0.0, 0, 0.0), style=style)
        colors = btn["frameColor"]
        assert len(colors) == 4, style
        assert btn["relief"] is not None, style
        # (ready, press, rollover, disabled) -- a test indexes [0].
        assert tuple(colors[0]) != tuple(colors[2]), style
        assert tuple(colors[3]) == uc.BTN_DISABLED, style


def test_button_state_ordering_matches_directgui(base):
    btn = make_button(base.aspect2d, "OK", (0.0, 0, 0.0), style="danger")
    ready, press, rollover, disabled = btn["frameColor"]
    assert tuple(ready) == uc.BTN_DANGER
    assert tuple(press) == uc.BTN_DANGER_PRESS
    assert tuple(rollover) == uc.BTN_DANGER_HOVER
    assert tuple(disabled) == uc.BTN_DISABLED


def test_button_keeps_extra_args_and_frame_size_overrides(base):
    seen = []
    btn = make_button(
        base.aspect2d,
        "PICK",
        (0.1, 0.2),  # 2-tuple positions still supported
        lambda kind: seen.append(kind),
        extra_args=["jet"],
        frame_size=(-1.0, 1.0, -0.4, 0.8),
    )
    assert btn.get_x() == pytest.approx(0.1)
    assert btn.get_z() == pytest.approx(0.2)
    assert tuple(btn["frameSize"]) == (-1.0, 1.0, -0.4, 0.8)
    btn["command"](*btn["extraArgs"])
    assert seen == ["jet"]


def test_set_button_enabled_dims_the_label_too(base):
    btn = make_button(base.aspect2d, "LAUNCH", (0.0, 0, 0.0), style="primary")
    uc.set_button_enabled(btn, False)
    assert btn["state"] == DGG.DISABLED
    assert _text_color(btn) == pytest.approx(uc.TEXT_DISABLED, abs=1e-6)
    uc.set_button_enabled(btn, True)
    assert btn["state"] == DGG.NORMAL
    assert _text_color(btn) == pytest.approx(uc.TEXT_STRONG, abs=1e-6)


def test_style_existing_button_reskins_in_place(base):
    btn = make_button(base.aspect2d, "MODE", (0.0, 0, 0.0))
    uc.style_existing_button(btn, style="toggle")
    assert tuple(btn["frameColor"][0]) == uc.BTN_TOGGLE_ON
    assert btn["relief"] is not None


# --- entries -------------------------------------------------------------


def test_style_existing_entry_applies_chrome(base):
    from direct.gui.DirectGui import DirectEntry

    entry = DirectEntry(parent=base.aspect2d, initialText="x")
    uc.style_existing_entry(entry)
    bg = entry["frameColor"][0]
    assert bg[0] < 0.2 and bg[1] < 0.2 and bg[2] < 0.2
    assert entry["relief"] is not None


# --- kv rows -------------------------------------------------------------


def test_make_kv_row_shares_a_baseline_and_aligns_the_value(base):
    panel = make_panel(base.aspect2d, frame_size=(0.0, 0.9, -0.4, 0.0))
    row = uc.make_kv_row(panel, "ALTITUDE", "12000", pos=(0.06, 0, -0.1), width=0.6)
    assert row.key["text"] == "ALTITUDE"
    assert row.value["text"] == "12000"
    # Same baseline, value pushed out to the right edge of the row.
    assert row.key.get_z() == pytest.approx(row.value.get_z())
    assert row.value.get_x() - row.key.get_x() == pytest.approx(0.6)
    assert row.node.get_x() == pytest.approx(0.06)
    assert row.node.get_z() == pytest.approx(-0.1)

    row.set_value("13500", fg=uc.STATUS_WARNING)
    assert row.value["text"] == "13500"
    assert _text_color(row.value) == pytest.approx(uc.STATUS_WARNING, abs=1e-6)
    row.set_key("ALT")
    assert row.key["text"] == "ALT"

    row.hide()
    assert row.node.is_hidden()
    row.show()
    assert not row.node.is_hidden()
    row.destroy()
    assert row.node.is_empty()


def test_make_kv_row_left_aligned_value_stacks_under_the_key(base):
    row = uc.make_kv_row(
        base.aspect2d,
        "ID",
        "JET-01",
        pos=(0.0, 0, 0.0),
        value_align=TextNode.ALeft,
    )
    assert row.value.get_x() == pytest.approx(row.key.get_x())


def test_kv_rows_on_the_row_rhythm_do_not_overlap(base):
    stack = uc.Stack(-0.1, x=0.06)
    rows = [
        uc.make_kv_row(base.aspect2d, f"K{i}", str(i), pos=stack.pos())
        for i in range(4)
    ]
    zs = [row.node.get_z() for row in rows]
    assert zs == sorted(zs, reverse=True)
    gaps = [zs[i] - zs[i + 1] for i in range(len(zs) - 1)]
    assert all(gap >= uc.TYPE_LABEL for gap in gaps)


# --- rules, badges, sections --------------------------------------------


def test_make_rule_is_thin_and_non_degenerate(base):
    rule = uc.make_rule(base.aspect2d, pos=(0.0, 0, 0.0), width=0.5)
    left, right, bottom, top = rule["frameSize"]
    assert right - left == pytest.approx(0.5)
    assert 0 < top - bottom < uc.SPACE_1


def test_make_rule_anchors(base):
    left_rule = uc.make_rule(base.aspect2d, pos=(0.0, 0, 0.0), width=0.4)
    right_rule = uc.make_rule(
        base.aspect2d, pos=(0.0, 0, 0.0), width=0.4, anchor="right"
    )
    center = uc.make_rule(
        base.aspect2d, pos=(0.0, 0, 0.0), width=0.4, anchor="center"
    )
    assert left_rule["frameSize"][0] == pytest.approx(0.0)
    assert right_rule["frameSize"][1] == pytest.approx(0.0)
    assert center["frameSize"][0] == pytest.approx(-0.2)


def test_make_badge_tone_colors_and_updates(base):
    hostile = uc.make_badge(base.aspect2d, "CONTESTED", pos=(0.0, 0, 0.0), tone="hostile")
    assert hostile["text"] == "CONTESTED"
    assert _text_color(hostile) == pytest.approx(uc.STATUS_HOSTILE, abs=1e-6)
    assert hostile["frameColor"][3] > 0.5
    assert hostile["relief"] is not None

    friendly = uc.make_status_chip(
        base.aspect2d, "TRAINING", pos=(0.0, 0, -0.05), tone="friendly"
    )
    assert _text_color(friendly) == pytest.approx(uc.STATUS_FRIENDLY, abs=1e-6)
    assert tuple(friendly["frameColor"]) != tuple(hostile["frameColor"])

    uc.set_badge(hostile, "LIVE", tone="warning")
    assert hostile["text"] == "LIVE"
    assert _text_color(hostile) == pytest.approx(uc.STATUS_WARNING, abs=1e-6)


def test_badge_fill_is_darker_than_its_text(base):
    badge = uc.make_badge(base.aspect2d, "OK", pos=(0.0, 0, 0.0), tone="friendly")
    fill = badge["frameColor"]
    fg = _text_color(badge)
    assert sum(fill[:3]) < sum(fg[:3])


def test_make_section_pairs_a_bracket_label_with_a_rule(base):
    section = uc.make_section(
        base.aspect2d, "[ FORCES ]", pos=(0.06, 0, -0.1), width=0.6
    )
    assert section.label["text"] == "[ FORCES ]"
    assert section.rule["frameSize"][1] == pytest.approx(0.6)
    assert section.rule.get_z() < section.label.get_z()
    assert section.next_z < section.rule.get_z()
    section.destroy()


# --- field grid ----------------------------------------------------------


def test_make_field_grid_lays_out_sixteen_fields_without_collisions(base):
    panel = make_panel(base.aspect2d, frame_size=(0.0, 0.9, -1.2, 0.0))
    keys = [f"field_{i}" for i in range(16)]
    grid = uc.make_field_grid(
        panel,
        [(key, key.upper()) for key in keys],
        pos=(0.06, 0, -0.1),
        width=0.8,
        columns=2,
    )
    assert grid.rows == 8
    assert grid.columns == 2
    assert list(grid.entries) == keys
    assert len(grid.labels) == 16

    slots = {
        (round(entry.get_x(), 4), round(entry.get_z(), 4))
        for entry in grid.entries.values()
    }
    assert len(slots) == 16  # nothing stacked on top of anything else

    zs = sorted({round(entry.get_z(), 4) for entry in grid.entries.values()})
    gaps = [zs[i + 1] - zs[i] for i in range(len(zs) - 1)]
    assert all(gap >= uc.TYPE_MICRO for gap in gaps)

    # A label never overlaps its own entry horizontally.
    for key in keys:
        assert grid.entries[key].get_x() > grid.labels[key].get_x()

    assert grid.height == pytest.approx(8 * uc.ROW_H_TIGHT)
    assert grid.bottom_z == pytest.approx(-0.1 - 8 * uc.ROW_H_TIGHT)


def test_field_grid_values_round_trip(base):
    grid = uc.make_field_grid(
        base.aspect2d,
        ["seed", "jets"],
        pos=(0.0, 0, 0.0),
        initial={"seed": "7"},
    )
    assert grid.get("seed") == "7"
    assert grid.get("jets") == ""
    assert grid.get("missing") == ""
    grid.set_value("jets", "4")
    assert grid.values() == {"seed": "7", "jets": "4"}
    grid.hide()
    assert grid.node.is_hidden()
    grid.show()
    grid.destroy()
    assert grid.node.is_empty()
    assert grid.entries == {}


def test_field_grid_accepts_plain_string_fields(base):
    grid = uc.make_field_grid(base.aspect2d, ["a", "b", "c"], pos=(0.0, 0, 0.0))
    assert grid.labels["a"]["text"] == "a"
    assert grid.rows == 3


def test_field_grid_entries_stay_inside_their_column(base):
    """A left-column well must not reach the right-column label."""
    width = 0.80
    gap = 0.02
    grid = uc.make_field_grid(
        base.aspect2d,
        [("left", "Objectives"), ("right", "Corvette EN")],
        pos=(0.0, 0, 0.0),
        width=width,
        columns=2,
        gap=gap,
        label_ratio=0.60,
        entry_chars=20,
        scale=uc.TYPE_MICRO,
    )
    left = grid.entries["left"]
    right_label = grid.labels["right"]
    well = left.guiItem.get_frame()
    scale = float(left.get_scale()[0])
    right_edge = left.get_x() + float(well[1]) * scale
    assert right_edge <= right_label.get_x() + 1e-3
    assert left.hasScissor()


def test_make_field_row_shares_a_baseline(base):
    label, entry = uc.make_field_row(
        base.aspect2d, "Seed", pos=(0.06, 0, -0.2), initial="99"
    )
    assert label["text"] == "Seed"
    assert entry.get() == "99"
    assert label.get_z() == pytest.approx(entry.get_z())
    assert entry.get_x() > label.get_x()


# --- fonts are wired into the factories ----------------------------------


def test_chrome_widgets_use_the_bundled_font_when_available(base):
    from viewer3d import fonts

    if fonts.ui_font() is None:
        pytest.skip("bundled font not loadable in this environment")
    # Panda3D hands back a fresh Python wrapper per call, so compare by name.
    ui_name = fonts.ui_font().get_name()
    mono_name = fonts.mono_font().get_name()
    label = make_header(base.aspect2d, "[ X ]", pos=(0.0, 0, 0.0))
    assert label["text_font"].get_name() == ui_name
    btn = make_button(base.aspect2d, "GO", (0.0, 0, 0.0))
    assert btn["text_font"].get_name() == ui_name
    value = uc.make_label(base.aspect2d, "123", pos=(0.0, 0, 0.0), mono=True)
    assert value["text_font"].get_name() == mono_name
    assert mono_name != ui_name


def test_chrome_widgets_construct_without_any_font(base, monkeypatch):
    """A stripped build (no assets/fonts) must still build every widget."""
    monkeypatch.setattr(uc, "ui_font", lambda: None)
    monkeypatch.setattr(uc, "ui_font_regular", lambda: None)
    monkeypatch.setattr(uc, "mono_font", lambda: None)
    panel = make_panel(base.aspect2d, frame_size=(0.0, 0.5, -0.4, 0.0), title="[ T ]")
    assert panel._chrome_title["text"] == "[ T ]"
    assert make_button(panel, "GO", (0.1, 0, -0.2))["text"] == "GO"
    assert uc.make_label(panel, "v", pos=(0.1, 0, -0.25), mono=True)["text"] == "v"
    assert uc.make_entry(panel, pos=(0.1, 0, -0.3), width=4, initial="1").get() == "1"
    assert uc.make_badge(panel, "LIVE", pos=(0.1, 0, -0.35))["text"] == "LIVE"
