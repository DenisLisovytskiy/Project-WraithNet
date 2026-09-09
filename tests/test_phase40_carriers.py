"""Phase 40: naval movement, carriers as dynamic airbases, recovery SM."""

from __future__ import annotations

import copy
import math

import pytest

from wraithnet import config
from wraithnet.bases import BaseAssignment, HOME_BASE_ID, default_home_airbase
from wraithnet.naval import (
    NAVAL_FRAME_CATALOG,
    CarrierSpec,
    all_water_terrain,
    build_navigable_water_mask,
    connected_on_mask,
    position_navigable,
    step_carrier_movement,
    validate_carrier_placement,
)
from wraithnet.objectives import MissionObjective
from wraithnet.scenario_schema import (
    migrate_v4_to_v5,
    migrate_v5_to_v6,
    new_scenario_data,
    scenario_from_data,
)
from wraithnet.terrain import TerrainCell, TerrainGrid, all_plains_terrain
from wraithnet.world import WraithNetModel


def _water_with_land_barrier(
    width: float = 50.0,
    height: float = 50.0,
    cell_size: float = 5.0,
    *,
    land_cols: set[int] | None = None,
) -> TerrainGrid:
    """Mostly water map with optional land columns forming a barrier/channel."""
    terrain = all_water_terrain(width, height, cell_size=cell_size)
    land_cols = land_cols or set()
    cells = []
    for r in range(terrain.rows):
        row = []
        for c in range(terrain.columns):
            if c in land_cols:
                row.append(TerrainCell(terrain_type="PLAINS"))
            else:
                row.append(TerrainCell(terrain_type="WATER"))
        cells.append(row)
    return terrain.model_copy(update={"cells": cells})


def _carrier_scenario(
    *,
    carrier_pos: tuple[float, float] = (25.0, 25.0),
    carrier_heading: float = 0.0,
    hold: bool = True,
    route: list[tuple[float, float]] | None = None,
    jet_pos: tuple[float, float] | None = None,
    alternate: str | None = HOME_BASE_ID,
    frame_type: str = "ESCORT_CARRIER",
    terrain: TerrainGrid | None = None,
) -> object:
    terrain = terrain or all_water_terrain(50.0, 50.0, cell_size=5.0)
    # Place a fixed home base on a synthetic land pad: patch one plains cell
    # near (2,2) so GROUND base validation passes.
    cells = [list(row) for row in terrain.cells]
    # world (2,2) → cell (0,0) at cell_size=5
    cells[0][0] = TerrainCell(terrain_type="PLAINS")
    terrain = terrain.model_copy(update={"cells": cells})

    home = (2.0, 2.0)
    jet_xy = jet_pos or (carrier_pos[0] + 10.0, carrier_pos[1])
    data = new_scenario_data(
        grid_width=terrain.world_width,
        grid_height=terrain.world_height,
        terrain=terrain,
        home_base=home,
        fixed_airbases=[default_home_airbase(home)],
        carriers=[
            CarrierSpec(
                unit_id="Carrier_1",
                position=carrier_pos,
                heading=carrier_heading,
                frame_type=frame_type,  # type: ignore[arg-type]
                route=list(route or []),
                hold_position=hold,
            )
        ],
        jets=[
            {
                "agent_id": "Jet_1",
                "position": jet_xy,
                "payload": {"JDAM": 1},
                "frame_type": "STRIKE_FIGHTER",
                "base_assignment": BaseAssignment(
                    primary_base_id="Carrier_1",
                    alternate_base_id=alternate,
                ).model_dump(),
            }
        ],
        targets=[
            {
                "target_id": "Target_A",
                "position": (12.0, 12.0),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
        sam_sites=[],
        jammers=[],
        mission_objectives=[
            MissionObjective(
                objective_id="OBJ_DESTROY",
                objective_type="DESTROY",
                subject_ids=["Target_A"],
                required=True,
            ).model_dump(),
            MissionObjective(
                objective_id="OBJ_PROTECT_CV",
                objective_type="PROTECT",
                subject_ids=["Carrier_1"],
                required=False,
            ).model_dump(),
        ],
    )
    # Target must be on GROUND — put a plains cell under (12,12).
    cells = [list(row) for row in data["terrain"].cells]
    # (12,12) with cell_size 5 → col=2,row=2
    cells[2][2] = TerrainCell(terrain_type="PLAINS")
    data["terrain"] = data["terrain"].model_copy(update={"cells": cells})
    return scenario_from_data(data)


def test_migrate_v5_to_v6_adds_empty_carriers():
    raw = copy.deepcopy(config.SCENARIO)
    from wraithnet.scenario_schema import (
        migrate_v1_to_v2,
        migrate_v2_to_v3,
        migrate_v3_to_v4,
    )

    v5 = migrate_v4_to_v5(
        migrate_v3_to_v4(migrate_v2_to_v3(migrate_v1_to_v2(dict(raw))))
    )
    assert v5["scenario_version"] == 5
    assert "carriers" not in v5
    v6 = migrate_v5_to_v6(copy.deepcopy(v5))
    assert v6["scenario_version"] == 6
    assert v6["carriers"] == []

    scenario = scenario_from_data(config.SCENARIO)
    assert scenario.scenario_version == 9
    assert scenario.carriers == []


def test_default_poc_has_no_carriers_required():
    model = WraithNetModel()
    assert model.carriers == {}
    assert HOME_BASE_ID in model.base_positions
    assert model.jets["Jet_1"].base_assignment.primary_base_id == HOME_BASE_ID


def test_carrier_rejects_land_spawn():
    terrain = all_plains_terrain(50.0, 50.0)
    carrier = CarrierSpec(unit_id="CV", position=(25.0, 25.0), heading=0.0)
    errors = validate_carrier_placement(terrain, carrier)
    assert errors


def test_footprint_rejects_one_cell_channel():
    # Land columns leave a single water column — too narrow for fleet carrier.
    terrain = _water_with_land_barrier(land_cols={1, 3, 4, 5, 6, 7, 8, 9})
    # Water only in col 0 and 2. Cell centers at x=2.5 and x=12.5.
    frame = NAVAL_FRAME_CATALOG["FLEET_CARRIER"]
    mask = build_navigable_water_mask(terrain, frame.footprint_radius)
    # Center of col 2 may be inflated-blocked from adjacent land.
    pos = (12.5, 12.5)
    assert not position_navigable(terrain, mask, pos) or not connected_on_mask(
        terrain, mask, (2.5, 12.5), pos
    )


def test_carrier_moves_only_on_water_and_respects_turn_speed():
    terrain = all_water_terrain(50.0, 50.0)
    from wraithnet.naval import CarrierState

    frame = NAVAL_FRAME_CATALOG["ESCORT_CARRIER"]
    carrier = CarrierState(
        unit_id="Carrier_1",
        position=(10.0, 10.0),
        heading=0.0,  # +y
        frame_type="ESCORT_CARRIER",
        route=[(10.0, 40.0)],
        hold_position=False,
        hp=frame.hp,
        hp_max=frame.hp,
        prev_position=(10.0, 10.0),
    )
    start = carrier.position
    step_carrier_movement(carrier, terrain, others=[carrier])
    moved = math.dist(start, carrier.position)
    assert moved <= frame.max_speed + 1e-9
    assert carrier.position[0] == pytest.approx(10.0, abs=0.01)
    # Must remain on water.
    assert position_navigable(
        terrain,
        build_navigable_water_mask(terrain, frame.footprint_radius),
        carrier.position,
    )


def test_carrier_cannot_cross_land_barrier():
    terrain = _water_with_land_barrier(land_cols={5})
    from wraithnet.naval import CarrierState

    frame = NAVAL_FRAME_CATALOG["ESCORT_CARRIER"]
    # Start west of barrier (col 2 center ~12.5), try to go east past col 5.
    carrier = CarrierState(
        unit_id="Carrier_1",
        position=(12.5, 25.0),
        heading=90.0,  # +x
        frame_type="ESCORT_CARRIER",
        route=[(40.0, 25.0)],
        hold_position=False,
        hp=frame.hp,
        hp_max=frame.hp,
        prev_position=(12.5, 25.0),
    )
    for _ in range(40):
        step_carrier_movement(carrier, terrain, others=[carrier])
    # Barrier at col 5 (x 25-30); carrier must not cross into x>30 navigable.
    assert carrier.position[0] < 30.0


def test_dynamic_rtb_tracks_moving_carrier():
    scenario = _carrier_scenario(
        carrier_pos=(20.0, 20.0),
        hold=False,
        route=[(20.0, 40.0)],
        jet_pos=(20.0, 28.0),
        alternate=HOME_BASE_ID,
    )
    model = WraithNetModel(scenario=scenario)
    jet = model.jets["Jet_1"]
    jet.is_rtb = True
    jet.recovery_phase = "RTB"
    jet.fuel = 100.0

    before = model.base_positions["Carrier_1"]
    model.step({})
    after = model.base_positions["Carrier_1"]
    # Carrier advanced along +y route; registry tracks live deck, not spawn.
    assert after[1] > before[1]
    assert after == model.carriers["Carrier_1"].position
    # Recovery destination uses current carrier state (deck or astern approach).
    dest = model._recovery_destination(jet)
    assert math.dist(dest, after) <= config.RECOVERY_APPROACH_STANDOFF + 1e-6


def test_recovery_approach_then_capture_envelope():
    scenario = _carrier_scenario(
        carrier_pos=(25.0, 25.0),
        carrier_heading=0.0,
        hold=True,
        jet_pos=(25.0, 25.0 + 7.0),  # within approach radius 8
        alternate=HOME_BASE_ID,
    )
    model = WraithNetModel(scenario=scenario)
    jet = model.jets["Jet_1"]
    jet.is_rtb = True
    jet.heading = 0.0
    jet.desired_heading = 0.0
    jet.altitude = 2.0
    jet.desired_altitude = 2.0
    jet.fuel = 100.0
    jet.recovery_phase = "RTB"

    model.step({})
    assert jet.recovery_phase == "APPROACH"

    # Place on deck with matching velocity for capture.
    model.space.move_agent(jet, (25.0, 25.0))
    jet.altitude = 2.0
    jet.heading = 0.0
    jet.velocity_xy = (0.0, 0.0)
    carrier = model.carriers["Carrier_1"]
    carrier.prev_position = carrier.position
    assert model._try_recover_jet(jet)
    assert "Jet_1" in model.jets_returned_to_base
    assert model.carrier_recoveries == 1


def test_recovery_rejects_heading_and_speed_outside_tolerance():
    scenario = _carrier_scenario(hold=True, jet_pos=(25.0, 25.0))
    model = WraithNetModel(scenario=scenario)
    jet = model.jets["Jet_1"]
    jet.is_rtb = True
    jet.recovery_phase = "APPROACH"
    jet.altitude = 2.0
    model.space.move_agent(jet, (25.0, 25.0))

    jet.heading = 90.0  # 90° off carrier heading 0
    jet.velocity_xy = (0.0, 0.0)
    assert not model._try_recover_jet(jet)

    jet.heading = 0.0
    jet.velocity_xy = (3.0, 0.0)  # relative speed > 2
    assert not model._try_recover_jet(jet)


def test_destroyed_carrier_falls_back_to_alternate():
    scenario = _carrier_scenario(alternate=HOME_BASE_ID)
    model = WraithNetModel(scenario=scenario)
    jet = model.jets["Jet_1"]
    assert jet.active_base_id == "Carrier_1"

    model.carriers["Carrier_1"].hp = 0
    model.carriers["Carrier_1"].destroyed = True
    model._refresh_base_positions()

    base = model._ensure_active_base(jet)
    assert base == HOME_BASE_ID
    assert jet.active_base_id == HOME_BASE_ID
    assert not jet.recovery_abort


def test_destroyed_carrier_without_alternate_aborts():
    scenario = _carrier_scenario(alternate=None)
    model = WraithNetModel(scenario=scenario)
    jet = model.jets["Jet_1"]
    model.carriers["Carrier_1"].hp = 0
    model.carriers["Carrier_1"].destroyed = True
    model._refresh_base_positions()

    assert model._ensure_active_base(jet) is None
    assert jet.recovery_abort
    assert model.mission_aborted


def test_protect_objective_fails_when_carrier_destroyed():
    scenario = _carrier_scenario()
    # Make PROTECT required so destruction fails the mission.
    objs = []
    for o in scenario.mission_objectives:
        if o.objective_id == "OBJ_PROTECT_CV":
            objs.append(o.model_copy(update={"required": True}))
        else:
            objs.append(o)
    scenario = scenario.model_copy(update={"mission_objectives": objs})
    model = WraithNetModel(scenario=scenario)
    model.carriers["Carrier_1"].hp = 0
    model.carriers["Carrier_1"].destroyed = True
    from wraithnet.objectives import evaluate_mission

    destroyed = {"Carrier_1": True, "Target_A": False}
    ev = evaluate_mission(
        model.mission_objectives,
        tick=1,
        destroyed_ids=destroyed,
        returned_ids=set(),
        friendly_can_progress=True,
    )
    assert ev.objective_status["OBJ_PROTECT_CV"] == "FAILED"
    assert ev.result == "FAILURE"


def test_carrier_registers_in_base_registry():
    scenario = _carrier_scenario()
    model = WraithNetModel(scenario=scenario)
    assert "Carrier_1" in model.base_positions
    assert model.base_position("Carrier_1") == pytest.approx((25.0, 25.0))
