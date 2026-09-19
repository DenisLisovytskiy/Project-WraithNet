"""Procedural 3D terrain tiles: rolling plains, pine forest, swell water, windowed city."""

from __future__ import annotations

import pytest

pytest.importorskip("panda3d")

from panda3d.core import NodePath, loadPrcFileData

loadPrcFileData("", "window-type none")
loadPrcFileData("", "audio-library-name null")
loadPrcFileData("", "notify-level error")

from viewer3d.terrain_mesh import build_terrain_root, terrain_batch_node_count
from wraithnet.terrain import TerrainCell, TerrainGrid


def _grid() -> TerrainGrid:
    cells = [[TerrainCell(terrain_type="PLAINS") for _ in range(4)] for _ in range(4)]
    cells[0][0] = TerrainCell(terrain_type="WATER")
    cells[0][1] = TerrainCell(terrain_type="WATER")
    cells[1][0] = TerrainCell(terrain_type="FOREST")
    cells[1][1] = TerrainCell(terrain_type="FOREST")
    cells[2][2] = TerrainCell(terrain_type="CITY", building_height=10.0)
    cells[2][3] = TerrainCell(terrain_type="CITY", building_height=16.0)
    return TerrainGrid(
        world_width=20.0,
        world_height=20.0,
        terrain_edit_seed=7,
        cell_size=5.0,
        columns=4,
        rows=4,
        cells=cells,
    )


def _extent(node: NodePath):
    box = node.get_tight_bounds()
    assert box is not None
    lo, hi = box
    return hi.x - lo.x, hi.y - lo.y, hi.z - lo.z


def test_terrain_batches_stay_bounded_and_untextured():
    root = NodePath("t")
    terrain = build_terrain_root(root, _grid())
    assert terrain_batch_node_count(terrain) <= 8
    for tag in (
        "terrain-water",
        "terrain-plains",
        "terrain-forest-base",
        "terrain-city-base",
        "terrain-trees",
        "terrain-buildings",
        "terrain-windows",
    ):
        node = terrain.find(f"**/{tag}")
        assert not node.is_empty(), tag
        assert node.get_tag("textured-sprite") != "1"


def test_water_has_swell_not_a_flat_slab():
    root = NodePath("t")
    terrain = build_terrain_root(root, _grid())
    water = terrain.find("**/terrain-water")
    _w, _l, h = _extent(water)
    assert h > 0.08
    assert water.get_tag("water-anim") == "1"


def test_plains_roll_and_forest_pines_are_taller_than_scrub():
    root = NodePath("t")
    terrain = build_terrain_root(root, _grid())
    plains = terrain.find("**/terrain-plains")
    trees = terrain.find("**/terrain-trees")
    _pw, _pl, ph = _extent(plains)
    _tw, _tl, th = _extent(trees)
    assert ph > 0.08
    assert th > 2.0
    assert th > ph * 4


def test_city_blocks_have_windows():
    root = NodePath("t")
    terrain = build_terrain_root(root, _grid())
    windows = terrain.find("**/terrain-windows")
    buildings = terrain.find("**/terrain-buildings")
    assert not windows.is_empty()
    geom = windows.node()
    vdata = geom.get_geom(0).get_vertex_data()
    assert vdata.get_num_rows() >= 64
    _bw, _bl, bh = _extent(buildings)
    assert bh > 10.0
