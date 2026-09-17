"""Procedural ship meshes: escort vs fleet carrier, light vs standard corvette."""

from __future__ import annotations

import pytest

pytest.importorskip("panda3d")

from panda3d.core import BillboardEffect, NodePath, loadPrcFileData

loadPrcFileData("", "window-type none")
loadPrcFileData("", "audio-library-name null")
loadPrcFileData("", "notify-level error")

from viewer3d.scene import SceneHandles, sync_static_unit_glyphs
from viewer3d.textures import (
    attach_corvette_light,
    attach_corvette_standard,
    attach_escort_carrier,
    attach_fleet_carrier,
    attach_ship_mesh,
)
from wraithnet.trace_schemas import CarrierFrame, CorvetteFrame

TEAL = (0.62, 0.78, 0.86, 1.0)
RUST = (0.80, 0.42, 0.34, 1.0)


def _extent(node: NodePath):
    box = node.get_tight_bounds()
    assert box is not None
    lo, hi = box
    return hi.x - lo.x, hi.y - lo.y, hi.z - lo.z


def test_fleet_carrier_is_longer_wider_and_taller_than_escort():
    root = NodePath("cv")
    escort = attach_escort_carrier(root, "escort", TEAL)
    fleet = attach_fleet_carrier(root, "fleet", TEAL)
    ew, el, eh = _extent(escort)
    fw, fl, fh = _extent(fleet)
    assert fl > el
    assert fw > ew
    assert fh > eh
    assert escort.get_effect(BillboardEffect) is None
    assert fleet.get_effect(BillboardEffect) is None


def test_standard_corvette_is_beamier_and_taller_than_light():
    root = NodePath("ff")
    light = attach_corvette_light(root, "light", RUST)
    standard = attach_corvette_standard(root, "std", RUST)
    lw, ll, lh = _extent(light)
    sw, sl, sh = _extent(standard)
    assert sw > lw
    assert sl > ll
    assert sh > lh  # second mast + taller superstructure


def test_attach_ship_mesh_tags_all_naval_frames():
    root = NodePath("dispatch")
    assert attach_ship_mesh(root, "a", "ESCORT_CARRIER", TEAL).get_tag("ship-mesh") == (
        "ESCORT_CARRIER"
    )
    assert attach_ship_mesh(root, "b", "FLEET_CARRIER", TEAL).get_tag("ship-mesh") == (
        "FLEET_CARRIER"
    )
    assert attach_ship_mesh(root, "c", "CORVETTE_LIGHT", RUST).get_tag("ship-mesh") == (
        "CORVETTE_LIGHT"
    )
    assert attach_ship_mesh(root, "d", "CORVETTE_STANDARD", RUST).get_tag(
        "ship-mesh"
    ) == "CORVETTE_STANDARD"
    assert attach_ship_mesh(root, "e", "BATTLESHIP", TEAL) is None


def test_scene_uses_distinct_ship_meshes_not_billboards():
    root = NodePath("sea")
    handles = SceneHandles(root=root, fx_root=root.attach_new_node("fx"))
    sync_static_unit_glyphs(
        handles,
        carriers=[
            CarrierFrame(
                unit_id="CVE_1",
                position=(10.0, 10.0),
                heading=0.0,
                hp=12,
                hp_max=12,
                frame_type="ESCORT_CARRIER",
            ),
            CarrierFrame(
                unit_id="CV_1",
                position=(20.0, 10.0),
                heading=0.0,
                hp=20,
                hp_max=20,
                frame_type="FLEET_CARRIER",
            ),
        ],
        corvettes=[
            CorvetteFrame(
                unit_id="FFL_1",
                position=(12.0, 20.0),
                heading=0.0,
                hp=4,
                hp_max=4,
                frame_type="CORVETTE_LIGHT",
            ),
            CorvetteFrame(
                unit_id="FF_1",
                position=(22.0, 20.0),
                heading=0.0,
                hp=6,
                hp_max=6,
                frame_type="CORVETTE_STANDARD",
            ),
        ],
    )
    escort = handles.carriers["CVE_1"].find("**/glyph-CVE_1")
    fleet = handles.carriers["CV_1"].find("**/glyph-CV_1")
    light = handles.corvettes["FFL_1"].find("**/glyph-FFL_1")
    standard = handles.corvettes["FF_1"].find("**/glyph-FF_1")
    assert escort.get_tag("ship-mesh") == "ESCORT_CARRIER"
    assert fleet.get_tag("ship-mesh") == "FLEET_CARRIER"
    assert light.get_tag("ship-mesh") == "CORVETTE_LIGHT"
    assert standard.get_tag("ship-mesh") == "CORVETTE_STANDARD"
    for node in (escort, fleet, light, standard):
        assert node.find("**/sym-sprite").is_empty()
        assert node.get_effect(BillboardEffect) is None
    _, el, _ = _extent(escort)
    _, fl, _ = _extent(fleet)
    lw, _, lh = _extent(light)
    sw, _, sh = _extent(standard)
    assert fl > el
    assert sw > lw
    assert sh > lh
