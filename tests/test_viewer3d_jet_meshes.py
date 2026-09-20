"""Procedural jet meshes: flying-wing bomber and simplified strike fighter."""

from __future__ import annotations

import pytest

pytest.importorskip("panda3d")

from panda3d.core import (
    BillboardEffect,
    ColorAttrib,
    GeomVertexReader,
    LineSegs,
    NodePath,
    TransparencyAttrib,
    loadPrcFileData,
)

loadPrcFileData("", "window-type none")
loadPrcFileData("", "audio-library-name null")
loadPrcFileData("", "notify-level error")

from viewer3d.scene import build_static_scene
from viewer3d.textures import (
    ENEMY,
    FRIENDLY,
    attach_heavy_bomber,
    attach_interceptor,
    attach_jet_mesh,
    attach_strike_fighter,
)
from wraithnet.trace_schemas import TraceHeader

CYAN = (80 / 255, 200 / 255, 255 / 255, 1.0)
FRAMES = ("HEAVY_BOMBER", "STRIKE_FIGHTER", "INTERCEPTOR")


def _fill_geom(glyph: NodePath):
    fill = glyph.find("**/sym-fill")
    assert not fill.is_empty()
    return fill.node().get_geoms()[0]


def _baked_colors(glyph: NodePath) -> list[tuple[float, float, float]]:
    reader = GeomVertexReader(_fill_geom(glyph).get_vertex_data(), "color")
    out = []
    while not reader.is_at_end():
        c = reader.get_data4()
        out.append((c[0], c[1], c[2]))
    return out


def _bounds(node: NodePath):
    box = node.get_tight_bounds()
    assert box is not None
    return box[0], box[1]


def _extent(node: NodePath):
    lo, hi = _bounds(node)
    return hi.x - lo.x, hi.y - lo.y, hi.z - lo.z


def _header(**overrides) -> TraceHeader:
    data = dict(
        grid_width=100.0,
        grid_height=100.0,
        sensor_radius=65.0,
        jam_radius=12.0,
        sam_engagement_range=15.0,
        strike_range=1.0,
        jet_ids=["Bomber_1", "Fighter_1", "Dart_1"],
        initial_payload={
            "Bomber_1": {"JDAM": 1, "AIM9X": 0},
            "Fighter_1": {"JDAM": 1, "AIM9X": 0},
            "Dart_1": {"JDAM": 1, "AIM9X": 0},
        },
        target_ids=[],
        target_types={},
        target_hp_required={},
        sam_site_ids=[],
        jammer_positions={},
        target_positions={},
        jet_frame_types={
            "Bomber_1": "HEAVY_BOMBER",
            "Fighter_1": "STRIKE_FIGHTER",
            "Dart_1": "INTERCEPTOR",
        },
    )
    data.update(overrides)
    return TraceHeader(**data)


def test_heavy_bomber_is_a_wide_flying_wing():
    root = NodePath("bomber-root")
    glyph = attach_heavy_bomber(root, "glyph-b", CYAN)
    width, length, height = _extent(glyph)
    assert glyph.get_tag("jet-mesh") == ""  # tag is applied by attach_jet_mesh
    assert width > length  # kite/flying wing, not a slender fighter
    assert height > 0.15  # real thickness, not a billboard card
    assert glyph.get_effect(BillboardEffect) is None


def test_interceptor_is_smaller_and_sharper_than_the_strike_fighter():
    root = NodePath("int")
    fighter = attach_strike_fighter(root, "f", CYAN)
    interceptor = attach_interceptor(root, "i", CYAN)
    fw, fl, _fh = _extent(fighter)
    iw, il, _ih = _extent(interceptor)
    assert iw < fw  # cropped delta vs larger strike wings
    assert il < fl or iw / max(il, 1e-6) < fw / max(fl, 1e-6)


def test_strike_fighter_is_taller_and_narrower_than_the_bomber():
    root = NodePath("cmp")
    bomber = attach_heavy_bomber(root, "b", CYAN)
    fighter = attach_strike_fighter(root, "f", CYAN)
    bw, _bl, bh = _extent(bomber)
    fw, _fl, fh = _extent(fighter)
    assert bw > fw
    assert fh > bh  # twin tails vs tailless flying wing


def test_attach_jet_mesh_tags_all_catalog_frames():
    root = NodePath("dispatch")
    bomber = attach_jet_mesh(root, "g1", "HEAVY_BOMBER", CYAN)
    fighter = attach_jet_mesh(root, "g2", "STRIKE_FIGHTER", CYAN)
    interceptor = attach_jet_mesh(root, "g3", "INTERCEPTOR", CYAN)
    assert bomber is not None and bomber.get_tag("jet-mesh") == "HEAVY_BOMBER"
    assert fighter is not None and fighter.get_tag("jet-mesh") == "STRIKE_FIGHTER"
    assert interceptor is not None and interceptor.get_tag("jet-mesh") == "INTERCEPTOR"
    assert attach_jet_mesh(root, "g4", "BIPLANE", CYAN) is None


def test_scene_uses_meshes_not_jpeg_billboards():
    root = NodePath("scene-root")
    handles = build_static_scene(root, _header())
    bomber = handles.jet_glyphs["Bomber_1"]
    fighter = handles.jet_glyphs["Fighter_1"]
    interceptor = handles.jet_glyphs["Dart_1"]
    assert bomber.get_tag("jet-mesh") == "HEAVY_BOMBER"
    assert fighter.get_tag("jet-mesh") == "STRIKE_FIGHTER"
    assert interceptor.get_tag("jet-mesh") == "INTERCEPTOR"
    assert bomber.find("**/sym-sprite").is_empty()
    assert fighter.find("**/sym-sprite").is_empty()
    assert interceptor.find("**/sym-sprite").is_empty()
    assert bomber.get_effect(BillboardEffect) is None
    assert fighter.get_effect(BillboardEffect) is None
    assert interceptor.get_effect(BillboardEffect) is None


def test_jets_bake_shaded_paint_instead_of_one_flat_faction_color():
    root = NodePath("paint")
    for frame in FRAMES:
        glyph = attach_jet_mesh(root, f"p-{frame}", frame, FRIENDLY)
        colors = _baked_colors(glyph)
        assert len(set(colors)) > 10, f"{frame} is flat-painted, not shaded"
        lum = sorted(0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in colors)
        # Dark spine through lit leading edges: a real range, not near-black mush.
        assert lum[-1] - lum[0] > 0.15, f"{frame} has no spine-to-tip ramp"
        assert lum[len(lum) // 2] > 0.06, f"{frame} reads as an empty outline"


def test_faction_still_reads_off_the_airframe_not_just_the_keyline():
    root = NodePath("faction")
    for frame in FRAMES:
        friendly = _baked_colors(attach_jet_mesh(root, f"f-{frame}", frame, FRIENDLY))
        hostile = _baked_colors(attach_jet_mesh(root, f"h-{frame}", frame, ENEMY))
        green = sum(g - r for r, g, _b in friendly) / len(friendly)
        red = sum(r - g for r, g, _b in hostile) / len(hostile)
        assert green > 0.02, f"friendly {frame} lost its teal tint"
        assert red > 0.02, f"hostile {frame} lost its rust tint"


def test_fill_color_format_matches_the_keyline_sibling():
    """A v3c4 fill beside a v3cp LineSegs outline renders red and blue swapped."""
    segs = LineSegs("probe")
    segs.move_to(0, 0, 0)
    segs.draw_to(1, 0, 0)
    keyline = segs.create().get_geoms()[0]
    expected = keyline.get_vertex_data().get_format().get_column("color").get_numeric_type()
    root = NodePath("fmt")
    for frame in FRAMES:
        fmt = _fill_geom(attach_jet_mesh(root, f"x-{frame}", frame, CYAN)).get_vertex_data()
        assert fmt.get_format().get_column("color").get_numeric_type() == expected


def test_a_prioritised_set_color_still_overrides_the_baked_paint():
    """scene.py flashes jam/RTB/destroyed state via set_color on the group."""
    from viewer3d.textures.geom import restore_vertex_paint

    root = NodePath("flash")
    glyph = attach_jet_mesh(root, "flashy", "STRIKE_FIGHTER", FRIENDLY)
    fill = glyph.find("**/sym-fill")
    assert fill.get_net_state().get_attrib(ColorAttrib).get_color_type() == (
        ColorAttrib.T_vertex
    )
    glyph.set_color(1.0, 0.35, 1.0, 1.0, 1)  # priority 1, as scene.py does
    assert fill.get_net_state().get_attrib(ColorAttrib).get_color_type() == ColorAttrib.T_flat
    restore_vertex_paint(glyph)
    assert fill.get_net_state().get_attrib(ColorAttrib).get_color_type() == (
        ColorAttrib.T_vertex
    )


def test_overlapping_jets_keep_vertex_paint():
    """Co-located jets must not fall back to a flat faction blob."""
    root = NodePath("stack")
    a = attach_jet_mesh(root, "Jet_A", "STRIKE_FIGHTER", FRIENDLY)
    b = attach_jet_mesh(root, "Jet_B", "INTERCEPTOR", CYAN)
    a.set_pos(0.0, 0.0, 0.0)
    b.set_pos(0.05, 0.05, 0.0)  # clipped
    for glyph in (a, b):
        fill = glyph.find("**/sym-fill")
        assert fill.get_tag("vertex-paint") == "1"
        assert fill.get_net_state().get_attrib(ColorAttrib).get_color_type() == (
            ColorAttrib.T_vertex
        )
        assert fill.get_transparency() == TransparencyAttrib.MNone
        # Group stays white — no solid cyan/magenta waiting to show through.
        c = glyph.get_color()
        assert c[0] == pytest.approx(1.0)
        assert c[1] == pytest.approx(1.0)
        assert c[2] == pytest.approx(1.0)
