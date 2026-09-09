"""Terrain schema, passability, and 2.5D LOS tests (Phase 34)."""

from __future__ import annotations

import copy
import math

import pytest
from pydantic import ValidationError

from wraithnet import config
from wraithnet.broker import MessageBroker
from wraithnet.scenario_io import load_scenario, save_scenario
from wraithnet.scenario_schema import (
    ScenarioConfig,
    _default_scenario,
    migrate_v1_to_v2,
    new_scenario_data,
    scenario_from_data,
)
from wraithnet.terrain import (
    CITY_HEIGHT_MAX,
    CITY_HEIGHT_MIN,
    OutOfTerrainBoundsError,
    TerrainCell,
    TerrainGrid,
    all_plains_terrain,
    cell_center,
    has_line_of_sight,
    is_passable,
    segment_supercover_cells,
    validate_position_for_domain,
    world_to_cell,
)
from wraithnet.world import WraithNetModel


def _grid_with_cities(
    cities: dict[tuple[int, int], float],
    *,
    width: float = 100.0,
    height: float = 100.0,
    cell_size: float = 5.0,
    forests: set[tuple[int, int]] | None = None,
    water: set[tuple[int, int]] | None = None,
) -> TerrainGrid:
    grid = all_plains_terrain(width, height, cell_size)
    cells = [[copy.deepcopy(cell) for cell in row] for row in grid.cells]
    for (col, row), h in cities.items():
        cells[row][col] = TerrainCell(terrain_type="CITY", building_height=h)
    for col, row in forests or ():
        cells[row][col] = TerrainCell(terrain_type="FOREST")
    for col, row in water or ():
        cells[row][col] = TerrainCell(terrain_type="WATER")
    return TerrainGrid(
        world_width=width,
        world_height=height,
        cell_size=cell_size,
        columns=grid.columns,
        rows=grid.rows,
        cells=cells,
        terrain_edit_seed=grid.terrain_edit_seed,
    )


def _scenario_on(terrain: TerrainGrid, **overrides) -> ScenarioConfig:
    from wraithnet.bases import default_home_airbase
    from wraithnet.objectives import synthesize_legacy_destroy_objective

    data = new_scenario_data(
        grid_width=terrain.world_width,
        grid_height=terrain.world_height,
        terrain=terrain,
        jets=[
            {
                "agent_id": "Jet_1",
                "position": (10.0, 10.0),
                "payload": {"JDAM": 2, "AIM9X": 0},
            }
        ],
        targets=[
            {
                "target_id": "Target_A",
                "position": (50.0, 50.0),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
        sam_sites=[],
        jammers=[],
        home_base=(2.0, 2.0),
    )
    data.update(overrides)
    data["terrain"] = terrain
    data["grid_width"] = terrain.world_width
    data["grid_height"] = terrain.world_height

    home = data.get("home_base", (2.0, 2.0))
    if isinstance(home, list):
        home = (float(home[0]), float(home[1]))
    data["home_base"] = home
    # Resync bases/objectives when callers override entities without those fields.
    if "fixed_airbases" not in overrides:
        data["fixed_airbases"] = [default_home_airbase(home)]
    if "mission_objectives" not in overrides:
        subject_ids: list[str] = []
        for target in data.get("targets", []) or []:
            subject_ids.append(
                target["target_id"] if isinstance(target, dict) else target.target_id
            )
        for sam in data.get("sam_sites", []) or []:
            subject_ids.append(
                sam["target_id"] if isinstance(sam, dict) else sam.target_id
            )
        data["mission_objectives"] = (
            [synthesize_legacy_destroy_objective(subject_ids)] if subject_ids else []
        )
    return scenario_from_data(data)


# --- 1. schema and legacy all-plains migration ---


def test_legacy_scenario_migrates_to_all_plains_v2():
    migrated = migrate_v1_to_v2(dict(config.SCENARIO))
    assert migrated["scenario_version"] == 2
    scenario = scenario_from_data(config.SCENARIO)
    assert scenario.scenario_version == 9
    assert scenario.terrain.columns == math.ceil(config.GRID_WIDTH / 5.0)
    assert scenario.terrain.rows == math.ceil(config.GRID_HEIGHT / 5.0)
    assert all(
        c.terrain_type == "PLAINS" and c.building_height is None
        for row in scenario.terrain.cells
        for c in row
    )


def test_direct_model_validate_rejects_legacy_dict():
    with pytest.raises(ValidationError):
        ScenarioConfig.model_validate(config.SCENARIO)


def test_rejects_unsupported_future_scenario_version():
    data = new_scenario_data(
        jets=[{"agent_id": "J", "position": (1.0, 1.0), "payload": {"JDAM": 1}}],
        targets=[
            {
                "target_id": "T",
                "position": (2.0, 2.0),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
    )
    data["scenario_version"] = 99
    with pytest.raises(ValueError, match="Unsupported scenario_version"):
        scenario_from_data(data)


def test_rejects_terrain_dimensions_mismatch():
    terrain = all_plains_terrain(50.0, 50.0)
    with pytest.raises(ValidationError, match="terrain world dimensions"):
        scenario_from_data(
            new_scenario_data(
                grid_width=100.0,
                grid_height=100.0,
                terrain=terrain,
                jets=[
                    {
                        "agent_id": "J",
                        "position": (1.0, 1.0),
                        "payload": {"JDAM": 1},
                    }
                ],
                targets=[
                    {
                        "target_id": "T",
                        "position": (2.0, 2.0),
                        "hp_required": 1,
                        "target_type": "HARDENED_BUNKER",
                    }
                ],
            )
        )


def test_city_height_bounds_and_non_city_forbids_height():
    with pytest.raises(ValidationError):
        TerrainCell(terrain_type="CITY", building_height=None)
    with pytest.raises(ValidationError):
        TerrainCell(terrain_type="CITY", building_height=CITY_HEIGHT_MIN - 0.1)
    with pytest.raises(ValidationError):
        TerrainCell(terrain_type="CITY", building_height=CITY_HEIGHT_MAX + 0.1)
    with pytest.raises(ValidationError):
        TerrainCell(terrain_type="PLAINS", building_height=10.0)
    ok = TerrainCell(terrain_type="CITY", building_height=10.0)
    assert ok.building_height == 10.0


def test_all_plains_has_no_shared_mutable_defaults():
    a = all_plains_terrain(20.0, 20.0)
    b = all_plains_terrain(20.0, 20.0)
    a.cells[0][0] = TerrainCell(terrain_type="WATER")
    assert b.cells[0][0].terrain_type == "PLAINS"


# --- 2. world/cell boundary mapping ---


def test_world_to_cell_max_edge_and_oob():
    terrain = all_plains_terrain(100.0, 100.0, cell_size=5.0)
    assert world_to_cell(terrain, 0.0, 0.0) == (0, 0)
    assert world_to_cell(terrain, 100.0, 100.0) == (19, 19)
    assert world_to_cell(terrain, 99.9, 4.9) == (19, 0)
    with pytest.raises(OutOfTerrainBoundsError):
        world_to_cell(terrain, -0.01, 0.0)
    with pytest.raises(OutOfTerrainBoundsError):
        world_to_cell(terrain, 0.0, 100.01)


def test_world_to_cell_clipped_edge_on_non_divisible_map():
    terrain = all_plains_terrain(102.0, 100.0, cell_size=5.0)
    assert terrain.columns == 21
    assert world_to_cell(terrain, 102.0, 50.0) == (20, 10)
    cx, cy = cell_center(terrain, 20, 0)
    assert cx == (100.0 + 102.0) / 2.0
    assert cy == 2.5


# --- 3. passability matrix ---


@pytest.mark.parametrize(
    "terrain_type,domain,expected",
    [
        ("WATER", "AIR", True),
        ("WATER", "GROUND", False),
        ("WATER", "SURFACE", True),
        ("PLAINS", "AIR", True),
        ("PLAINS", "GROUND", True),
        ("PLAINS", "SURFACE", False),
        ("FOREST", "AIR", True),
        ("FOREST", "GROUND", True),
        ("FOREST", "SURFACE", False),
        ("CITY", "AIR", True),
        ("CITY", "GROUND", True),
        ("CITY", "SURFACE", False),
    ],
)
def test_passability_matrix(terrain_type, domain, expected):
    cells = [[TerrainCell(terrain_type="PLAINS")]]
    if terrain_type == "CITY":
        cells = [[TerrainCell(terrain_type="CITY", building_height=8.0)]]
    elif terrain_type != "PLAINS":
        cells = [[TerrainCell(terrain_type=terrain_type)]]
    terrain = TerrainGrid(
        world_width=5.0,
        world_height=5.0,
        cell_size=5.0,
        columns=1,
        rows=1,
        cells=cells,
    )
    assert is_passable(terrain, (2.5, 2.5), domain) is expected


# --- 4. supercover symmetry ---


def test_supercover_symmetry_and_corner():
    terrain = all_plains_terrain(100.0, 100.0, cell_size=5.0)
    a = (2.5, 2.5)
    b = (27.5, 17.5)
    fwd = segment_supercover_cells(terrain, a, b)
    rev = segment_supercover_cells(terrain, b, a)
    assert fwd[0] == world_to_cell(terrain, *a)
    assert fwd[-1] == world_to_cell(terrain, *b)
    assert rev[0] == world_to_cell(terrain, *b)
    assert rev[-1] == world_to_cell(terrain, *a)
    assert set(fwd) == set(rev)

    # Exact diagonal through cell corners
    c = (0.0, 0.0)
    d = (20.0, 20.0)
    diag = segment_supercover_cells(terrain, c, d)
    assert set(diag) == set(segment_supercover_cells(terrain, d, c))
    assert len(diag) >= 5


# --- 5–8. LOS ---


def test_low_ground_los_blocked_by_city():
    # Cells (2,1) and (4,1) are endpoints; (3,1) is intermediate city.
    terrain = _grid_with_cities({(3, 1): 10.0})
    start = (12.5, 7.5, 1.8)
    end = (22.5, 7.5, 1.8)
    assert has_line_of_sight(terrain, start, end) is False
    assert has_line_of_sight(terrain, end, start) is False


def test_high_los_passes_over_shorter_building():
    terrain = _grid_with_cities({(3, 1): 10.0})
    start = (12.5, 7.5, 20.0)
    end = (22.5, 7.5, 20.0)
    assert has_line_of_sight(terrain, start, end) is True
    assert has_line_of_sight(terrain, end, start) is True


def test_start_and_end_city_cells_ignored():
    terrain = _grid_with_cities({(2, 1): 25.0, (4, 1): 25.0})
    start = (12.5, 7.5, 1.8)
    end = (22.5, 7.5, 1.8)
    assert has_line_of_sight(terrain, start, end) is True


def test_forest_does_not_block_los():
    terrain = _grid_with_cities({}, forests={(3, 1)})
    start = (12.5, 7.5, 1.8)
    end = (22.5, 7.5, 1.8)
    assert has_line_of_sight(terrain, start, end) is True


# --- 9–11. world adjudication ---


def test_observation_omits_blocked_targets():
    # Jet at (10,10), target at (50,50); place a tall city on the segment.
    terrain = _grid_with_cities({(6, 6): 25.0})
    scenario = _scenario_on(
        terrain,
        jets=[
            {
                "agent_id": "Jet_1",
                "position": (10.0, 10.0),
                "payload": {"JDAM": 1, "AIM9X": 0},
            }
        ],
        targets=[
            {
                "target_id": "Hidden",
                "position": (50.0, 50.0),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
    )
    model = WraithNetModel(scenario=scenario)
    # Clear plains control: same geometry without city must see the target.
    clear = _scenario_on(
        all_plains_terrain(100.0, 100.0),
        jets=scenario.jets,
        targets=scenario.targets,
        home_base=scenario.home_base,
    )
    clear_model = WraithNetModel(scenario=clear)
    assert {
        t.target_id
        for t in clear_model.build_observation("Jet_1", MessageBroker()).known_targets
    } == {"Hidden"}
    assert model.build_observation("Jet_1", MessageBroker()).known_targets == []


def test_strike_through_building_rejected_without_consuming_ammo():
    # cell_size 0.5 so an intermediate city fits inside JDAM range 1.0
    terrain = _grid_with_cities(
        {(3, 2): 25.0}, width=20.0, height=20.0, cell_size=0.5
    )
    # cells: col2=[1.0,1.5), col3=[1.5,2.0), col4=[2.0,2.5)
    scenario = _scenario_on(
        terrain,
        jets=[
            {
                "agent_id": "Jet_1",
                "position": (1.25, 1.25),
                "payload": {"JDAM": 1, "AIM9X": 0},
            }
        ],
        targets=[
            {
                "target_id": "Bunker",
                "position": (2.25, 1.25),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
        home_base=(5.0, 5.0),
    )
    model = WraithNetModel(scenario=scenario)
    jet = model.jets["Jet_1"]
    assert math.dist(jet.pos, model.targets["Bunker"].position) <= config.WEAPON_CATALOG[
        "JDAM"
    ].range
    assert not model.has_combat_los(
        jet.pos,
        jet.altitude,
        model.targets["Bunker"].position,
        config.GROUND_TARGET_HEIGHT,
    )
    assert jet.altitude == pytest.approx(config.JET_COMBAT_ALTITUDE)
    model._apply_strike(jet, "Bunker", "JDAM")
    assert jet.payload["JDAM"] == 1
    assert model.targets["Bunker"].hp_remaining == 1
    assert model.strike_log == []


def test_city_los_uses_jet_altitude_not_global_constant():
    """A low jet is blocked by a city that a cruise-altitude jet clears to ground."""
    # Building 5u: cruise(20)→ground clears; alt=3 does not (CITY min height is 4).
    terrain = _grid_with_cities({(3, 1): 5.0})
    scenario = _scenario_on(
        terrain,
        jets=[
            {
                "agent_id": "Jet_1",
                "position": (12.5, 7.5),
                "payload": {"JDAM": 1, "AIM9X": 0},
            }
        ],
        targets=[
            {
                "target_id": "Far",
                "position": (22.5, 7.5),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
        home_base=(5.0, 5.0),
    )
    model = WraithNetModel(scenario=scenario)
    jet = model.jets["Jet_1"]
    target = model.targets["Far"].position
    assert model.has_combat_los(
        jet.pos, jet.altitude, target, config.GROUND_TARGET_HEIGHT
    )
    jet.altitude = 3.0
    assert not model.has_combat_los(
        jet.pos, jet.altitude, target, config.GROUND_TARGET_HEIGHT
    )
    # Observation must also use jet.altitude (blocked at low Z).
    assert model.build_observation("Jet_1", MessageBroker()).known_targets == []
    jet.altitude = config.JET_FRAME_CATALOG[jet.frame_type].cruise_altitude
    assert {
        t.target_id
        for t in model.build_observation("Jet_1", MessageBroker()).known_targets
    } == {"Far"}


def test_legacy_sam_no_damage_through_blocked_los():
    # SAM col 6 (30–35), jet col 8 (40–45); intermediate city at col 7.
    terrain = _grid_with_cities({(7, 10): 25.0})
    scenario = _scenario_on(
        terrain,
        jets=[
            {
                "agent_id": "Jet_1",
                "position": (42.0, 52.5),
                "payload": {"JDAM": 1, "AIM9X": 0},
                "frame_type": "STRIKE_FIGHTER",
            }
        ],
        targets=[],
        sam_sites=[
            {
                "target_id": "SAM_1",
                "position": (32.0, 52.5),
                "hp_required": 1,
                "target_type": "SAM_SITE",
            }
        ],
    )
    model = WraithNetModel(scenario=scenario)
    assert math.dist(model.jets["Jet_1"].pos, model.sam_sites["SAM_1"].position) <= (
        config.SAM_ENGAGEMENT_RANGE
    )
    assert not model.has_combat_los(
        model.sam_sites["SAM_1"].position,
        config.SAM_SENSOR_HEIGHT,
        model.jets["Jet_1"].pos,
        model.jets["Jet_1"].altitude,
    )
    hp_before = model.jets["Jet_1"].hp
    model._apply_sam_damage()
    assert model.jets["Jet_1"].hp == hp_before


# --- 12. land entities reject water; jets do not ---


def test_land_entities_reject_water_jets_do_not():
    terrain = _grid_with_cities({}, water={(0, 0), (1, 1)})
    with pytest.raises(ValidationError, match="WATER"):
        _scenario_on(
            terrain,
            jets=[
                {
                    "agent_id": "Jet_1",
                    "position": (2.5, 2.5),  # water cell (0,0)
                    "payload": {"JDAM": 1},
                }
            ],
            targets=[
                {
                    "target_id": "T",
                    "position": (7.5, 7.5),  # water (1,1)
                    "hp_required": 1,
                    "target_type": "HARDENED_BUNKER",
                }
            ],
            home_base=(12.5, 12.5),
        )
    # Jet over water is fine if land entities are on plains.
    ok = _scenario_on(
        terrain,
        jets=[
            {
                "agent_id": "Jet_1",
                "position": (2.5, 2.5),
                "payload": {"JDAM": 1},
            }
        ],
        targets=[
            {
                "target_id": "T",
                "position": (22.5, 22.5),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
        home_base=(12.5, 12.5),
    )
    assert ok.jets[0].position == (2.5, 2.5)


def test_validate_position_for_domain_helper():
    terrain = _grid_with_cities({}, water={(0, 0)})
    validate_position_for_domain(terrain, (2.5, 2.5), "AIR")
    validate_position_for_domain(terrain, (2.5, 2.5), "SURFACE")
    with pytest.raises(ValueError, match="GROUND"):
        validate_position_for_domain(terrain, (2.5, 2.5), "GROUND")


# --- 13. serialization round-trip ---


def test_serialization_round_trip_preserves_cell_types_and_heights(tmp_path):
    terrain = _grid_with_cities(
        {(2, 3): 12.5, (4, 4): 7.0},
        forests={(1, 1)},
        water={(0, 5)},
    )
    scenario = _scenario_on(terrain)
    path = tmp_path / "terrain_scenario.json"
    save_scenario(scenario, path)
    loaded = load_scenario(path)
    assert loaded.terrain.model_dump() == scenario.terrain.model_dump()
    assert loaded.terrain.cells[3][2].terrain_type == "CITY"
    assert loaded.terrain.cells[3][2].building_height == 12.5
    assert loaded.terrain.cells[1][1].terrain_type == "FOREST"
    assert loaded.terrain.cells[5][0].terrain_type == "WATER"


def test_default_scenario_still_all_plains_compatible():
    scenario = _default_scenario()
    model = WraithNetModel(scenario=scenario)
    obs = model.build_observation("Jet_1", MessageBroker())
    assert {t.target_id for t in obs.known_targets} == {
        "Target_A",
        "Target_B",
        "SAM_Site_1",
    }
    # Injected in-range strike still works on plains.
    jet = model.jets["Jet_1"]
    model.space.move_agent(jet, (75.0, 75.0))
    before = jet.payload["JDAM"]
    model._apply_strike(jet, "Target_A", "JDAM")
    assert jet.payload["JDAM"] == before - 1
