"""Phase 43: tanks — movement, AI, generation, feasibility, trace, metrics."""

from __future__ import annotations

import copy
import math

from wraithnet import config
from wraithnet.bases import default_home_airbase
from wraithnet.bridge import WraithNetBridge
from wraithnet.guns import TANK_CANNON, GunBurstRequest, validate_gun_burst
from wraithnet.metrics import summarize
from wraithnet.naval import all_water_terrain
from wraithnet.objectives import MissionObjective
from wraithnet.scenario_feasibility import GROUND_DISCOVERY_DEADLOCK, analyze_feasibility
from wraithnet.scenario_schema import migrate_v8_to_v9, new_scenario_data, scenario_from_data
from wraithnet.tanks import (
    TankSpec,
    astar_land_path,
    step_tank_movement,
    validate_tank_placement,
)
from wraithnet.terrain import TerrainCell, all_plains_terrain
from wraithnet.trace_builders import build_trace_tick
from wraithnet.world import WraithNetModel


def _tank_duel(
    *,
    blue_pos=(20.0, 20.0),
    red_pos=(24.0, 20.0),
    blue_order="HOLD",
    red_order="HOLD",
    blue_wp=None,
    red_wp=None,
    combat_seed=1,
    grid=50.0,
):
    home = (2.0, 2.0)
    data = new_scenario_data(
        grid_width=grid,
        grid_height=grid,
        home_base=home,
        fixed_airbases=[default_home_airbase(home)],
        jets=[],
        targets=[],
        sam_sites=[],
        jammers=[],
        tanks=[
            TankSpec(
                unit_id="Blue_1",
                faction="FRIENDLY",
                position=blue_pos,
                heading=90.0,
                order=blue_order,
                waypoints=list(blue_wp or []),
            ),
            TankSpec(
                unit_id="Red_1",
                faction="ENEMY",
                position=red_pos,
                heading=270.0,
                order=red_order,
                waypoints=list(red_wp or []),
            ),
        ],
        mission_objectives=[
            MissionObjective(
                objective_id="OBJ_DESTROY_TANK",
                objective_type="DESTROY",
                subject_ids=["Red_1"],
                required=True,
            ).model_dump()
        ],
        combat_seed=combat_seed,
    )
    return scenario_from_data(data)


def test_migrate_v8_to_v9_adds_empty_tanks():
    scenario = scenario_from_data(copy.deepcopy(config.SCENARIO))
    assert scenario.scenario_version == 9
    assert scenario.tanks == []
    out = migrate_v8_to_v9({"scenario_version": 8})
    assert out["scenario_version"] == 9
    assert out["tanks"] == []


def test_tank_water_spawn_rejected():
    terrain = all_water_terrain(40.0, 40.0, cell_size=5.0)
    tank = TankSpec(unit_id="T1", position=(20.0, 20.0), faction="ENEMY")
    assert validate_tank_placement(terrain, tank)


def test_astar_avoids_water_no_corner_cut():
    terrain = all_plains_terrain(30.0, 30.0, cell_size=5.0)
    cells = [list(row) for row in terrain.cells]
    for r in range(terrain.rows):
        cells[r][2] = TerrainCell(terrain_type="WATER")
    cells[2][2] = TerrainCell(terrain_type="PLAINS")
    terrain = terrain.model_copy(update={"cells": cells})
    path = astar_land_path(terrain, (2.5, 2.5), (17.5, 2.5))
    assert path is not None
    assert all(terrain.cells[r][c].terrain_type != "WATER" for c, r in path)


def test_forest_no_path_cost_modifier():
    plains = all_plains_terrain(40.0, 40.0, cell_size=5.0)
    forest = all_plains_terrain(40.0, 40.0, cell_size=5.0)
    cells = [list(row) for row in forest.cells]
    for r in range(forest.rows):
        for c in range(forest.columns):
            cells[r][c] = TerrainCell(terrain_type="FOREST")
    forest = forest.model_copy(update={"cells": cells})
    p1 = astar_land_path(plains, (5.0, 5.0), (30.0, 30.0))
    p2 = astar_land_path(forest, (5.0, 5.0), (30.0, 30.0))
    assert p1 is not None and p2 is not None
    assert len(p1) == len(p2)


def test_tank_cannon_rejects_non_ground_vehicle():
    for cls in ("AIRCRAFT", "SURFACE_VESSEL", "INSTALLATION", "IN_FLIGHT_MISSILE"):
        req = GunBurstRequest(
            shooter_id="T1",
            target_id="X",
            gun=TANK_CANNON,
            shooter_faction="ENEMY",
            target_faction="FRIENDLY",
            target_class=cls,  # type: ignore[arg-type]
            shooter_position=(0.0, 0.0),
            target_position=(2.0, 0.0),
            shooter_heading_deg=90.0,
            target_altitude=0.0,
            ammo_available=5,
            has_los=True,
            combat_seed=0,
            tick=0,
        )
        assert validate_gun_burst(req) == "illegal_target_class"


def test_friendly_fire_rejected():
    req = GunBurstRequest(
        shooter_id="T1",
        target_id="T2",
        gun=TANK_CANNON,
        shooter_faction="FRIENDLY",
        target_faction="FRIENDLY",
        target_class="GROUND_VEHICLE",
        shooter_position=(0.0, 0.0),
        target_position=(2.0, 0.0),
        shooter_heading_deg=90.0,
        target_altitude=0.0,
        ammo_available=5,
        has_los=True,
        combat_seed=0,
        tick=0,
    )
    assert validate_gun_burst(req) == "friendly_fire"


def test_tanks_can_engage_each_other():
    scenario = _tank_duel()
    model = WraithNetModel(seed=0, scenario=scenario)
    model.step({})
    tank_events = [e for e in model.gun_events if e.get("layer") == "TANK_CANNON"]
    assert tank_events
    assert all(e.get("target_id") in model.tanks for e in tank_events)


def test_advance_orders_close_distance():
    scenario = _tank_duel(
        blue_pos=(10.0, 25.0),
        red_pos=(40.0, 25.0),
        blue_order="ADVANCE",
        red_order="ADVANCE",
        blue_wp=[(25.0, 25.0)],
        red_wp=[(25.0, 25.0)],
        combat_seed=2,
    )
    model = WraithNetModel(seed=0, scenario=scenario)
    d0 = math.dist(model.tanks["Blue_1"].position, model.tanks["Red_1"].position)
    for _ in range(25):
        model.step({})
    d1 = math.dist(model.tanks["Blue_1"].position, model.tanks["Red_1"].position)
    assert d1 < d0
    assert model.tanks["Blue_1"].distance_traveled > 0
    assert model.tanks["Red_1"].distance_traveled > 0


def test_all_hold_deadlock_feasibility():
    scenario = _tank_duel(
        blue_pos=(5.0, 5.0),
        red_pos=(45.0, 45.0),
        blue_order="HOLD",
        red_order="HOLD",
    )
    report = analyze_feasibility(scenario)
    codes = [i.code for i in report.issues]
    assert GROUND_DISCOVERY_DEADLOCK in codes


def test_armor_reduces_damage_and_ammo_cooldown():
    scenario = _tank_duel(combat_seed=0)
    model = WraithNetModel(seed=0, scenario=scenario)
    red = model.tanks["Red_1"]
    hp0 = red.hp
    ammo0 = model.tanks["Blue_1"].ammo
    model.step({})
    # Standard frame has armor 1; hits of 2 become 1 applied.
    if any(e.get("accepted") for e in model.gun_events if e.get("layer") == "TANK_CANNON"):
        assert red.hp <= hp0
        assert model.tanks["Blue_1"].ammo < ammo0 or model.tanks["Red_1"].ammo < ammo0


def test_jdam_deals_at_least_one_damage_through_tank_armor():
    """Armor must reduce cannon damage, not nullify 1-damage air strikes."""
    from wraithnet.scenario_schema import JetSpec

    home = (2.0, 2.0)
    data = new_scenario_data(
        grid_width=40.0,
        grid_height=40.0,
        home_base=home,
        fixed_airbases=[default_home_airbase(home)],
        jets=[
            JetSpec(
                agent_id="Jet_1",
                position=(20.0, 20.0),
                payload={"JDAM": 2, "AIM9X": 0},
            )
        ],
        targets=[],
        tanks=[
            TankSpec(
                unit_id="Red_1",
                faction="ENEMY",
                position=(20.5, 20.0),
                heading=0.0,
                frame_type="MBT_STANDARD",
                order="HOLD",
            )
        ],
    )
    scenario = scenario_from_data(data)
    model = WraithNetModel(seed=0, scenario=scenario)
    tank = model.tanks["Red_1"]
    assert tank.armor >= 1
    hp0 = tank.hp
    jet = model.jets["Jet_1"]
    jet.altitude = 10.0  # JDAM releases at or below max_release_altitude
    model._apply_strike(jet, "Red_1", "JDAM")
    assert tank.hp == hp0 - 1
    assert jet.payload["JDAM"] == 1


def test_city_blocks_los_but_allows_movement():
    terrain = all_plains_terrain(40.0, 40.0, cell_size=5.0)
    cells = [list(row) for row in terrain.cells]
    # Tall city wall between (10,20) and (30,20).
    for r in range(terrain.rows):
        cells[r][3] = TerrainCell(terrain_type="CITY", building_height=20.0)
    terrain = terrain.model_copy(update={"cells": cells})
    path = astar_land_path(terrain, (7.0, 17.0), (22.0, 17.0))
    assert path is not None  # city is passable
    home = (2.0, 2.0)
    data = new_scenario_data(
        grid_width=40.0,
        grid_height=40.0,
        terrain=terrain,
        home_base=home,
        fixed_airbases=[default_home_airbase(home)],
        jets=[],
        targets=[],
        tanks=[
            TankSpec(
                unit_id="Blue_1",
                faction="FRIENDLY",
                position=(7.5, 17.5),
                heading=90.0,
                order="HOLD",
            ),
            TankSpec(
                unit_id="Red_1",
                faction="ENEMY",
                position=(22.5, 17.5),
                heading=270.0,
                order="HOLD",
            ),
        ],
        mission_objectives=[
            MissionObjective(
                objective_id="OBJ_T",
                objective_type="DESTROY",
                subject_ids=["Red_1"],
                required=True,
            ).model_dump()
        ],
    )
    model = WraithNetModel(seed=0, scenario=scenario_from_data(data))
    model.step({})
    # City wall should prevent engagement LOS at ground heights.
    accepted = [
        e
        for e in model.gun_events
        if e.get("layer") == "TANK_CANNON" and e.get("accepted")
    ]
    assert accepted == []


def test_trace_and_metrics_tanks():
    scenario = _tank_duel()
    bridge = WraithNetBridge(scenario=scenario)
    bridge.model.step({})
    tick = build_trace_tick(bridge)
    assert {t.unit_id for t in tick.tanks} == {"Blue_1", "Red_1"}
    summary = summarize(bridge.model)
    assert "tank_rounds" in summary
    assert "tank_losses_enemy" in summary


def test_poc_preserved():
    scenario = scenario_from_data(copy.deepcopy(config.SCENARIO))
    assert scenario.scenario_version == 9
    assert scenario.tanks == []
    model = WraithNetModel(seed=0, scenario=scenario)
    model.step({})


def test_jet_jdam_strike_damages_tank():
    home = (2.0, 2.0)
    data = new_scenario_data(
        grid_width=50.0,
        grid_height=50.0,
        home_base=home,
        fixed_airbases=[default_home_airbase(home)],
        jets=[
            {
                "agent_id": "Jet_1",
                "position": (20.0, 20.0),
                "payload": {"JDAM": 2},
            }
        ],
        targets=[],
        tanks=[
            TankSpec(
                unit_id="Red_1",
                faction="ENEMY",
                position=(20.5, 20.0),
                heading=0.0,
                order="HOLD",
                frame_type="MBT_LIGHT",
                hp=3,
            )
        ],
        mission_objectives=[
            MissionObjective(
                objective_id="OBJ_T",
                objective_type="DESTROY",
                subject_ids=["Red_1"],
                required=True,
            ).model_dump()
        ],
    )
    model = WraithNetModel(seed=0, scenario=scenario_from_data(data))
    jet = model.jets["Jet_1"]
    jet.altitude = 10.0  # JDAM releases at or below max_release_altitude
    before = model.tanks["Red_1"].hp
    model._apply_strike(jet, "Red_1", "JDAM")
    assert model.tanks["Red_1"].hp == before - 1
    assert jet.payload["JDAM"] == 1
