"""Terrain painting helpers and Phase 36 editor/scene wiring."""

from __future__ import annotations

import copy

import pytest

from wraithnet.generation_schemas import TerrainGenerationParameters
from wraithnet.scenario_schema import new_scenario_data, scenario_from_data
from wraithnet.terrain import TerrainCell, TerrainGrid, all_plains_terrain
from wraithnet.terrain_generator import generate_terrain
from wraithnet.terrain_paint import (
    apply_paint_to_cells,
    cells_in_brush,
    deterministic_city_height,
    fill_terrain,
    invalid_ground_entities,
    relocate_invalid_entities,
    stroke_cells,
)
from wraithnet.trace_builders import catalog_header_fields
from wraithnet.trace_schemas import TraceHeader


def _plains(w: float = 100.0, h: float = 100.0, cell: float = 5.0) -> TerrainGrid:
    return all_plains_terrain(w, h, cell)


def test_brush_radius_clips_at_map_edges():
    terrain = _plains()
    cells = cells_in_brush(terrain, 0, 0, radius=5)
    assert all(0 <= c < terrain.columns and 0 <= r < terrain.rows for c, r in cells)
    assert (0, 0) in cells
    assert (-1, 0) not in cells


def test_stroke_supercover_no_gaps_on_diagonal():
    terrain = _plains()
    # Far diagonal samples — must include intermediate cells via supercover.
    samples = [(2.5, 2.5), (47.5, 47.5)]
    cells = stroke_cells(terrain, samples, radius=1)
    cols = {c for c, _ in cells}
    rows = {r for _, r in cells}
    assert len(cells) >= 10
    assert min(cols) <= 1 and max(cols) >= 8
    assert min(rows) <= 1 and max(rows) >= 8


def test_city_heights_persist_and_non_city_clears():
    terrain = _plains()
    city = apply_paint_to_cells(
        terrain,
        [(2, 2)],
        "CITY",
        city_height_mode="FIXED",
        fixed_city_height=12.0,
        stroke_sequence=0,
    )
    assert city.cells[2][2].terrain_type == "CITY"
    assert city.cells[2][2].building_height == pytest.approx(12.0)
    plains = apply_paint_to_cells(city, [(2, 2)], "PLAINS")
    assert plains.cells[2][2].terrain_type == "PLAINS"
    assert plains.cells[2][2].building_height is None


def test_random_city_height_deterministic():
    a = deterministic_city_height(42, 3, 4, 1)
    b = deterministic_city_height(42, 3, 4, 1)
    c = deterministic_city_height(42, 3, 4, 2)
    assert a == b
    assert a != c
    assert 4.0 <= a <= 25.0


def test_fill_and_generated_terrain():
    base = _plains()
    filled = fill_terrain(base, "FOREST", stroke_sequence=0)
    assert all(c.terrain_type == "FOREST" for row in filled.cells for c in row)
    gen = generate_terrain(
        100.0,
        100.0,
        TerrainGenerationParameters(water_fraction=0.15, forest_fraction=0.2, city_fraction=0.1),
        seed=7,
    )
    assert any(c.terrain_type == "WATER" for row in gen.cells for c in row)


def test_invalid_entities_listed_but_not_moved_by_paint():
    draft = new_scenario_data(
        targets=[{"target_id": "T1", "position": [2.5, 2.5], "hp_required": 1}],
        home_base=[7.5, 7.5],
    )
    draft["terrain"] = draft["terrain"].model_dump()
    draft["home_base"] = list(draft["home_base"])
    before_pos = list(draft["targets"][0]["position"])
    painted = apply_paint_to_cells(
        TerrainGrid.model_validate(draft["terrain"]),
        [(0, 0)],
        "WATER",
    )
    draft["terrain"] = painted.model_dump()
    issues = invalid_ground_entities(draft)
    assert any("T1" in label for label, _ in issues)
    assert draft["targets"][0]["position"] == before_pos


def test_deterministic_relocation():
    draft = new_scenario_data(
        targets=[{"target_id": "T1", "position": [2.5, 2.5], "hp_required": 1}],
        home_base=[7.5, 7.5],
    )
    draft = {**draft, "terrain": draft["terrain"].model_dump(), "home_base": list(draft["home_base"])}
    water = apply_paint_to_cells(
        TerrainGrid.model_validate(draft["terrain"]), [(0, 0)], "WATER"
    )
    draft["terrain"] = water.model_dump()
    a = relocate_invalid_entities(draft)
    b = relocate_invalid_entities(draft)
    assert a["targets"][0]["position"] == b["targets"][0]["position"]
    assert a["targets"][0]["position"] != [2.5, 2.5]
    assert not invalid_ground_entities(a)


def test_invalid_ground_covers_aaa_tanks_and_naval_without_relocating_ships():
    draft = new_scenario_data(
        targets=[],
        home_base=[7.5, 7.5],
        aaa_sites=[
            {
                "target_id": "AAA_1",
                "position": [2.5, 2.5],
                "hp_required": 2,
                "target_type": "AAA_SITE",
            }
        ],
        tanks=[
            {
                "unit_id": "Tank_1",
                "position": [2.5, 2.5],
                "faction": "ENEMY",
                "order": "HOLD",
            }
        ],
        carriers=[
            {
                "unit_id": "Carrier_1",
                "position": [12.5, 12.5],
                "frame_type": "ESCORT_CARRIER",
            }
        ],
        corvettes=[
            {
                "unit_id": "Corvette_1",
                "position": [17.5, 17.5],
                "frame_type": "CORVETTE_STANDARD",
                "loadout": {"SAM_RADAR": 8, "CIWS": 40, "DECK_GUN": 20},
            }
        ],
    )
    draft["terrain"] = draft["terrain"].model_dump()
    draft["home_base"] = list(draft["home_base"])
    # AAA/tank at (0,0) become invalid when painted water; ships on plains are invalid SURFACE.
    painted = apply_paint_to_cells(
        TerrainGrid.model_validate(draft["terrain"]),
        [(0, 0)],
        "WATER",
    )
    draft["terrain"] = painted.model_dump()
    issues = invalid_ground_entities(draft)
    labels = {label for label, _ in issues}
    assert any("AAA_1" in label for label in labels)
    assert any("Tank_1" in label for label in labels)
    assert any("Carrier_1" in label for label in labels)
    assert any("Corvette_1" in label for label in labels)

    before_carrier = list(draft["carriers"][0]["position"])
    before_corvette = list(draft["corvettes"][0]["position"])
    relocated = relocate_invalid_entities(draft)
    assert relocated["carriers"][0]["position"] == before_carrier
    assert relocated["corvettes"][0]["position"] == before_corvette
    # GROUND entities moved off water.
    assert relocated["aaa_sites"][0]["position"] != [2.5, 2.5]
    assert relocated["tanks"][0]["position"] != [2.5, 2.5]
    remaining = invalid_ground_entities(relocated)
    remaining_labels = {label for label, _ in remaining}
    assert not any("AAA_1" in label for label in remaining_labels)
    assert not any("Tank_1" in label for label in remaining_labels)
    # Naval still reported (not silently relocated).
    assert any("Carrier_1" in label for label in remaining_labels)
    assert any("Corvette_1" in label for label in remaining_labels)


def test_trace_header_round_trip_includes_terrain():
    from viewer3d.editor import scenario_to_header

    terrain = _plains()
    cells = [[copy.deepcopy(c) for c in row] for row in terrain.cells]
    cells[1][1] = TerrainCell(terrain_type="CITY", building_height=9.0)
    terrain = TerrainGrid(
        world_width=terrain.world_width,
        world_height=terrain.world_height,
        terrain_edit_seed=terrain.terrain_edit_seed,
        cell_size=terrain.cell_size,
        columns=terrain.columns,
        rows=terrain.rows,
        cells=cells,
    )
    config = scenario_from_data(
        new_scenario_data(
            jets=[{"agent_id": "J1", "position": [10.0, 10.0], "payload": {"JDAM": 1}}],
            targets=[{"target_id": "T1", "position": [40.0, 40.0], "hp_required": 1, "target_type": "HARDENED_BUNKER"}],
            terrain=terrain,
        )
    )
    header = scenario_to_header(config)
    assert header.trace_version == 3
    assert header.terrain is not None
    assert header.terrain.cells[1][1].terrain_type == "CITY"
    assert header.terrain.cells[1][1].building_height == pytest.approx(9.0)


pytest.importorskip("panda3d")


def test_scene_uses_header_terrain_and_bounded_batches():
    from panda3d.core import NodePath, loadPrcFileData

    loadPrcFileData("", "window-type none")
    loadPrcFileData("", "audio-library-name null")

    from viewer3d.scene import build_static_scene
    from viewer3d.terrain_mesh import resolve_header_terrain, terrain_batch_node_count

    terrain = generate_terrain(
        100.0,
        100.0,
        TerrainGenerationParameters(water_fraction=0.2, forest_fraction=0.25, city_fraction=0.15),
        seed=3,
    )
    header = TraceHeader(
        grid_width=100.0,
        grid_height=100.0,
        sensor_radius=20.0,
        jam_radius=15.0,
        sam_engagement_range=25.0,
        strike_range=30.0,
        jet_ids=[],
        initial_payload={},
        target_ids=[],
        target_types={},
        target_hp_required={},
        sam_site_ids=[],
        jammer_positions={},
        target_positions={},
        home_base=(10.0, 10.0),
        jet_frame_types={},
        terrain=terrain,
        trace_version=2,
        **catalog_header_fields(),
    )
    root = NodePath("r")
    handles = build_static_scene(root, header)
    assert handles.terrain_root is not None
    assert not handles.terrain_root.is_empty()
    assert resolve_header_terrain(header).model_dump() == terrain.model_dump()
    assert terrain_batch_node_count(handles.terrain_root) <= 8


def test_legacy_header_without_terrain_renders_plains():
    from panda3d.core import NodePath, loadPrcFileData

    loadPrcFileData("", "window-type none")
    loadPrcFileData("", "audio-library-name null")

    from viewer3d.terrain_mesh import resolve_header_terrain

    header = TraceHeader(
        grid_width=50.0,
        grid_height=50.0,
        sensor_radius=20.0,
        jam_radius=15.0,
        sam_engagement_range=25.0,
        strike_range=30.0,
        jet_ids=[],
        initial_payload={},
        target_ids=[],
        target_types={},
        target_hp_required={},
        sam_site_ids=[],
        jammer_positions={},
        target_positions={},
        home_base=(5.0, 5.0),
        jet_frame_types={},
        terrain=None,
        **catalog_header_fields(),
    )
    resolved = resolve_header_terrain(header)
    assert all(c.terrain_type == "PLAINS" for row in resolved.cells for c in row)


def test_editor_mode_switch_and_stroke_undo():
    from panda3d.core import loadPrcFileData

    loadPrcFileData("", "window-type none")
    loadPrcFileData("", "audio-library-name null")
    loadPrcFileData("", "notify-level error")

    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D(editor_mode=True, start_client=False)
    try:
        ed = app._editor
        assert ed._edit_mode == "ENTITY"
        ed.enter_terrain_mode()
        assert ed._edit_mode == "TERRAIN"
        ed._terrain_brush = "WATER"
        ed.terrain_paint_begin((2.5, 2.5))
        ed.terrain_paint_drag((7.5, 2.5))
        ed.terrain_paint_end()
        assert ed.scenario_draft["terrain"]["cells"][0][0]["terrain_type"] == "WATER"
        before_undo = copy.deepcopy(ed.scenario_draft["terrain"])
        ed._terrain_undo()
        assert ed.scenario_draft["terrain"] != before_undo
        ed._terrain_redo()
        assert ed.scenario_draft["terrain"] == before_undo
        # Painting must not relocate entities.
        ed.scenario_draft["targets"] = [
            {"target_id": "T1", "position": [2.5, 2.5], "hp_required": 1, "target_type": "HARDENED_BUNKER"}
        ]
        issues = invalid_ground_entities(ed.scenario_draft)
        assert issues
        pos_before = list(ed.scenario_draft["targets"][0]["position"])
        ed.rebuild_scene()
        assert ed.scenario_draft["targets"][0]["position"] == pos_before
        ed.enter_entity_mode()
        assert ed._edit_mode == "ENTITY"
    finally:
        try:
            app.destroy()
        except Exception:
            pass
