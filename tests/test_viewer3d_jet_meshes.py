"""Procedural jet meshes: flying-wing bomber and simplified strike fighter."""

from __future__ import annotations

import pytest

pytest.importorskip("panda3d")

from panda3d.core import BillboardEffect, NodePath, loadPrcFileData

loadPrcFileData("", "window-type none")
loadPrcFileData("", "audio-library-name null")
loadPrcFileData("", "notify-level error")

from viewer3d.scene import build_static_scene
from viewer3d.textures import (
    attach_heavy_bomber,
    attach_interceptor,
    attach_jet_mesh,
    attach_strike_fighter,
)
from wraithnet.trace_schemas import TraceHeader

CYAN = (80 / 255, 200 / 255, 255 / 255, 1.0)


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
