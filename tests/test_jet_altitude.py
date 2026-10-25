"""Relative altitude, obstacle avoidance and altitude tactics."""

from __future__ import annotations

import pytest

from wraithnet import config
from wraithnet.agents import JetAgentRuntime, transit_altitude
from wraithnet.broker import MessageBroker
from wraithnet.missiles import LaunchRequest
from wraithnet.scenario_schema import JetSpec, TargetSpec, new_scenario_data, scenario_from_data
from wraithnet.schemas import ActionIntent, ObservedGunThreat, TargetInfo
from wraithnet.terrain import (
    FOREST_CANOPY_HEIGHT,
    TerrainCell,
    all_plains_terrain,
    lookahead_required_altitude,
)
from wraithnet.weapons_release import attack_altitude, release_attitude_ok
from wraithnet.world import WraithNetModel


def _model(
    *,
    position=(10.0, 50.0),
    heading=90.0,
    city_cells=(),
    forest_cells=(),
    building_height=25.0,
    frame_type="STRIKE_FIGHTER",
) -> WraithNetModel:
    terrain = all_plains_terrain(100.0, 100.0)
    for col, row in city_cells:
        terrain.cells[row][col] = TerrainCell(
            terrain_type="CITY", building_height=building_height
        )
    for col, row in forest_cells:
        terrain.cells[row][col] = TerrainCell(terrain_type="FOREST")
    scenario = scenario_from_data(
        new_scenario_data(
            terrain=terrain,
            jets=[
                JetSpec(
                    agent_id="Jet_A",
                    position=position,
                    payload={"JDAM": 2},
                    frame_type=frame_type,
                    initial_heading=heading,
                )
            ],
            targets=[
                TargetSpec(
                    target_id="Bunker",
                    position=(90.0, 10.0),
                    hp_required=1,
                    target_type="HARDENED_BUNKER",
                )
            ],
        )
    )
    return WraithNetModel(scenario=scenario)


def _fly_low(model: WraithNetModel, ticks: int) -> list[float]:
    """Command 'as low as allowed' due east; return relative altitude per tick."""
    jet = model.jets["Jet_A"]
    agl = []
    for tick in range(ticks):
        model.step(
            {
                "Jet_A": ActionIntent(
                    agent_id="Jet_A",
                    tick=tick,
                    action_type="IDLE",
                    desired_heading=90.0,
                    desired_pitch_deg=0.0,
                    desired_altitude=0.0,
                )
            }
        )
        if "Jet_A" not in model.jets:
            break
        agl.append(model.relative_altitude(jet))
    return agl


def test_relative_altitude_is_height_above_roof_or_treetops():
    model = _model(position=(12.5, 52.5), city_cells=[(2, 10)], building_height=12.0)
    jet = model.jets["Jet_A"]
    assert model.relative_altitude(jet) == pytest.approx(jet.altitude - 12.0)
    forest = _model(position=(12.5, 52.5), forest_cells=[(2, 10)])
    fjet = forest.jets["Jet_A"]
    assert forest.relative_altitude(fjet) == pytest.approx(
        fjet.altitude - FOREST_CANOPY_HEIGHT
    )


def test_level_flight_eases_to_commanded_altitude():
    """Pitch-0 intents with desired_altitude actually change height."""
    model = _model()
    jet = model.jets["Jet_A"]
    frame = config.JET_FRAME_CATALOG[jet.frame_type]
    start = jet.altitude
    _fly_low(model, 1)
    assert jet.altitude == pytest.approx(start - frame.max_climb_per_tick)


def test_low_flier_climbs_over_tallest_building_without_crashing():
    # A wall of 25-tall buildings across the jet's eastbound track.
    wall = [(12, row) for row in range(20)]
    model = _model(city_cells=wall)
    agl = _fly_low(model, 30)
    assert model.crash_log == []
    assert "Jet_A" in model.jets
    assert min(agl) >= config.TERRAIN_CLEARANCE - 1e-9
    # It went low over open ground, then rose to clear the 25-tall roofs.
    assert min(agl) < 10.0
    assert model.jets["Jet_A"].pos[0] > 65.0  # past the wall


def test_treetops_respected_at_low_level():
    model = _model(forest_cells=[(c, 10) for c in range(20)])
    agl = _fly_low(model, 15)
    assert model.crash_log == []
    assert min(agl) >= config.TERRAIN_CLEARANCE - 1e-9


def test_lookahead_ignores_far_buildings_so_jets_can_fly_low():
    model = _model(city_cells=[(19, 19)])
    frame = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"]
    need = lookahead_required_altitude(
        model.terrain,
        (5.0, 5.0),
        reach_per_tick=frame.max_speed,
        climb_per_tick=frame.max_climb_per_tick,
        clearance=config.TERRAIN_CLEARANCE,
    )
    assert need <= 0.0


def test_crash_safety_net_destroys_jet_below_obstacle():
    model = _model(position=(12.5, 52.5), city_cells=[(2, 10)], building_height=12.0)
    jet = model.jets["Jet_A"]
    jet.altitude = 5.0  # forced inside the building (bypassing the floor)
    jet.desired_altitude = None
    model.step({})
    assert model.crash_log and model.crash_log[0]["agent_id"] == "Jet_A"
    assert jet.is_destroyed


def test_radar_sam_cannot_engage_below_clutter_floor():
    model = _model()
    jet = model.jets["Jet_A"]
    req = LaunchRequest(
        shooter_id="SAM_X",
        target_id="Jet_A",
        weapon_type="SAM_RADAR",
        shooter_position=(50.0, 50.0, 0.0),
        target_position=(10.0, 50.0, 4.0),
        shooter_faction="ENEMY",
        target_faction="FRIENDLY",
        target_class="AIRCRAFT",
        target_signature=config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].signature,
        ammo_available=1,
    )
    jet.altitude = config.SAM_RADAR_MIN_ENGAGEMENT_AGL - 1.0
    assert model._radar_clutter_reason(req) == "below_radar_clutter_floor"
    jet.altitude = config.SAM_RADAR_MIN_ENGAGEMENT_AGL + 1.0
    assert model._radar_clutter_reason(req) is None
    ir = LaunchRequest(**{**req.__dict__, "weapon_type": "SAM_IR"})
    jet.altitude = 4.0
    assert model._radar_clutter_reason(ir) is None


def test_unguided_release_requires_descent():
    jdam = config.WEAPON_CATALOG["JDAM"]
    assert release_attitude_ok(jdam, pitch_deg=0.0, roll_deg=0.0, altitude=20.0) == (
        False,
        "above_max_release_altitude",
    )
    assert release_attitude_ok(jdam, pitch_deg=0.0, roll_deg=0.0, altitude=13.0)[0]
    assert attack_altitude(jdam, 23.0) == pytest.approx(13.0)
    assert attack_altitude(config.WEAPON_CATALOG["AGM65"], 23.0) == 23.0


def _obs(model: WraithNetModel):
    return model.build_observation("Jet_A", MessageBroker())


def test_transit_altitude_follows_threat_picture():
    model = _model()
    obs = _obs(model)
    cruise = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].cruise_altitude
    assert transit_altitude(obs, []) == cruise
    sam = TargetInfo(
        target_id="SAM_1",
        position=(30.0, 50.0),
        hp_required=2,
        hp_remaining=2,
        target_type="SAM_SITE",
    )
    assert transit_altitude(obs, [sam]) == 0.0  # low: under surface radar
    far_sam = sam.model_copy(update={"position": (95.0, 95.0)})
    assert transit_altitude(obs, [far_sam]) == cruise  # high: sensors see further
    gun = ObservedGunThreat(
        threat_id="AAA_1", position=(30.0, 30.0), engagement_range=5.0, altitude_ceiling=18.0
    )
    guns = obs.model_copy(update={"observed_gun_threats": [gun]})
    assert transit_altitude(guns, [sam]) == pytest.approx(
        max(cruise, 18.0 + config.GUN_CEILING_MARGIN)
    )


def test_agent_descends_for_jdam_run_in_and_stays_high_far_out():
    model = _model(position=(80.0, 20.0))
    runtime = JetAgentRuntime("Jet_A", MessageBroker())
    runtime._commit("Bunker", 1, "JDAM")
    runtime.set_observation(_obs(model))
    runtime.refresh_world_view()
    near = runtime.decide_intent()
    assert near.desired_altitude == pytest.approx(13.0)

    far_model = _model(position=(5.0, 95.0))
    far = JetAgentRuntime("Jet_A", MessageBroker())
    far._commit("Bunker", 1, "JDAM")
    far.set_observation(_obs(far_model))
    far.refresh_world_view()
    assert far.decide_intent().desired_altitude == pytest.approx(
        config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].cruise_altitude
    )
